"""Verified off-server copies of consistent local SQLite snapshots."""
from __future__ import annotations

import base64
import errno
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import threading
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from . import db

OFFSITE_ROOT = Path('/offsite')
REMOTE_RETENTION = 30
MAX_MANIFEST_BYTES = 64 * 1024
MAX_OAUTH_STATES = 64
SCOPE = 'https://www.googleapis.com/auth/drive.file'
FOLDER_NAME = 'Indigo Stats Backups'
FOLDER_PROPERTY = {'indigoStats': 'backups-v1'}
SNAPSHOT_RE = re.compile(r'^\d{8}T\d{6}Z\.sqlite$')
_run_lock = threading.Lock()
_oauth_lock = threading.Lock()
_oauth_states: dict[str, tuple[str, float]] = {}

def token_file() -> Path:
    return db.DATA / 'secrets' / 'google-drive-token.json'


class BackupError(RuntimeError):
    pass


class BackupBusy(BackupError):
    pass


class RelinkRequired(BackupError):
    pass


@dataclass(frozen=True)
class RemoteSnapshot:
    filename: str
    checksum: str
    size: int
    created_at: int
    reference: str = ''
    schema_version: int = 0
    application_version: str = ''


def _secret(name: str) -> str | None:
    value = os.getenv(name, '').strip()
    if value:
        return value
    filename = os.getenv(name + '_FILE', '').strip()
    if not filename:
        return None
    try:
        return Path(filename).read_text(encoding='utf-8').strip()
    except OSError as exc:
        raise BackupError(f'{name} secret file is unavailable') from exc


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, RelinkRequired) or _auth_error(exc):
        return 'Google Drive must be linked again'
    if isinstance(exc, BackupError):
        return str(exc)
    return f'{type(exc).__name__}: transfer failed; will retry'


def _auth_error(exc: BaseException) -> bool:
    return (type(exc).__name__ == 'RefreshError'
            or getattr(getattr(exc, 'resp', None), 'status', None) == 401)


def _config() -> dict[str, Any]:
    raw = db.settings().get('offsite_backup', {'provider': 'disabled'})
    return raw if isinstance(raw, dict) else {'provider': 'disabled'}


def validate_config(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError('Backup configuration must be an object')
    provider = value.get('provider')
    allowed = {
        'disabled': {'provider'},
        'filesystem': {'provider'},
        's3': {'provider', 'bucket', 'prefix', 'region', 'endpoint', 'encryption'},
        'google_drive': {'provider'},
    }
    if not isinstance(provider, str) or provider not in allowed:
        raise ValueError('Invalid backup provider configuration')
    if not all(isinstance(key, str) for key in value) or set(value) - allowed[provider]:
        raise ValueError('Invalid backup provider configuration')
    if provider == 'disabled':
        return {'provider': 'disabled'}
    if provider == 'filesystem':
        return {'provider': 'filesystem'}
    if provider == 'google_drive':
        return {'provider': 'google_drive'}
    fields = ('bucket', 'prefix', 'region', 'endpoint', 'encryption')
    if any(key in value and not isinstance(value[key], str) for key in fields):
        raise ValueError('S3 configuration values must be strings')
    bucket = value.get('bucket', '').strip()
    if not bucket or len(bucket) > 255:
        raise ValueError('S3 bucket is required')
    prefix = value.get('prefix', 'indigo-stats').strip().strip('/')
    if '..' in PurePosixPath(prefix).parts or len(prefix) > 512:
        raise ValueError('Invalid S3 prefix')
    region = value.get('region', '').strip()
    if len(region) > 128:
        raise ValueError('Invalid S3 region')
    endpoint = value.get('endpoint', '').strip()
    if len(endpoint) > 2048:
        raise ValueError('Invalid S3 endpoint')
    if endpoint:
        parsed = urlparse(endpoint)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or '..' in PurePosixPath(parsed.path).parts):
            raise ValueError('Custom S3 endpoint must be HTTPS')
        endpoint = endpoint.rstrip('/')
    encryption = value.get('encryption', 'default')
    if encryption not in ('default', 'sse-s3', 'sse-kms'):
        raise ValueError('Invalid S3 encryption mode')
    return {'provider': 's3', 'bucket': bucket, 'prefix': prefix, 'region': region,
            'endpoint': endpoint, 'encryption': encryption}


def set_config(value: dict[str, Any]) -> dict[str, Any]:
    clean = validate_config(value)
    if not _run_lock.acquire(blocking=False):
        raise BackupBusy('An off-server backup is already running')
    try:
        changed = clean != _config()
        db.set_settings({'offsite_backup': clean})
        if changed:
            with db.connect() as con:
                con.execute("DELETE FROM job_status WHERE name='offsite_backup'")
        return clean
    finally:
        _run_lock.release()


def fingerprint(cfg: dict[str, Any]) -> str:
    stable = json.dumps(cfg, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(stable).hexdigest()


def checksum(path: Path) -> str:
    with path.open('rb') as source:
        return _stream_checksum(source)[0]


def _stream_checksum(source) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: source.read(1024 * 1024), b''):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _manifest_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(',', ':')).encode()


def manifest(path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(path)) as con:
        schema = con.execute('PRAGMA user_version').fetchone()[0]
    from .app import APP_VERSION
    return {'format': 1, 'filename': path.name, 'size': path.stat().st_size,
            'created_at': int(path.stat().st_mtime), 'schema_version': schema,
            'application_version': APP_VERSION, 'sha256': checksum(path)}


def _parse_manifest(payload: bytes | str) -> RemoteSnapshot:
    try:
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ValueError
        data = json.loads(payload)
        name = data['filename']
        digest = data['sha256']
        size = int(data['size'])
        created = int(data['created_at'])
        if data.get('format') != 1 or not SNAPSHOT_RE.fullmatch(name):
            raise ValueError
        if not re.fullmatch(r'[0-9a-f]{64}', digest) or size < 1:
            raise ValueError
        return RemoteSnapshot(name, digest, size, created, '', int(data.get('schema_version', 0)),
                              str(data.get('application_version', '')))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackupError('Remote backup manifest is invalid') from exc


class Provider:
    def probe(self) -> None: raise NotImplementedError
    def upload(self, path: Path, data: dict[str, Any]) -> str: raise NotImplementedError
    def list(self) -> list[RemoteSnapshot]: raise NotImplementedError
    def download(self, item: RemoteSnapshot, target: Path) -> None: raise NotImplementedError
    def delete(self, item: RemoteSnapshot) -> None: raise NotImplementedError


class FilesystemProvider(Provider):
    def __init__(self, root: Path = OFFSITE_ROOT):
        self.mount = root
        self.root = root / 'indigo-stats'

    def _validate_mount(self) -> None:
        if not self.mount.exists() or self.mount.is_symlink() or not os.path.ismount(self.mount):
            raise BackupError('The /offsite mount is missing or is not a distinct mount')
        resolved = self.mount.resolve()
        if resolved == db.DATA.resolve() or resolved.is_relative_to(db.DATA.resolve()):
            raise BackupError('The off-server mount must not be under /data')
        if not os.access(self.mount, os.W_OK | os.X_OK):
            raise BackupError('The /offsite mount is not writable')

    @staticmethod
    def _validate_name(name: str) -> None:
        if name != Path(name).name or not (
                SNAPSHOT_RE.fullmatch(name) or name.endswith('.sqlite.manifest.json')):
            raise BackupError('Invalid remote backup name')

    @contextmanager
    def _open_root(self, *, create: bool):
        self._validate_mount()
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0)
        mount_fd = os.open(self.mount, flags)
        root_fd = None
        try:
            if create:
                try:
                    os.mkdir('indigo-stats', mode=0o700, dir_fd=mount_fd)
                except FileExistsError:
                    pass
            try:
                root_fd = os.open('indigo-stats', flags, dir_fd=mount_fd)
            except FileNotFoundError:
                if create:
                    raise
                yield None
                return
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise BackupError('The off-server backup directory is unsafe') from exc
                raise
            yield root_fd
        finally:
            if root_fd is not None:
                os.close(root_fd)
            os.close(mount_fd)

    @staticmethod
    def _read(root_fd: int, name: str, *, limit: int | None = None) -> bytes:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(name, flags, dir_fd=root_fd)
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode) or (limit is not None and file_stat.st_size > limit):
                raise BackupError('Remote backup file is invalid')
            with os.fdopen(fd, 'rb', closefd=False) as source:
                payload = source.read() if limit is None else source.read(limit + 1)
                if limit is not None and len(payload) > limit:
                    raise BackupError('Remote backup file is invalid')
                return payload
        finally:
            os.close(fd)

    @staticmethod
    def _file_checksum(root_fd: int, name: str) -> tuple[str, int]:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(name, flags, dir_fd=root_fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise BackupError('Remote backup file is invalid')
            with os.fdopen(fd, 'rb', closefd=False) as source:
                return _stream_checksum(source)
        finally:
            os.close(fd)

    def probe(self) -> None:
        with self._open_root(create=False) as root_fd:
            path = self.root if root_fd is not None else self.mount
            if not os.access(path, os.W_OK | os.X_OK):
                raise BackupError('The /offsite mount is not writable')

    @staticmethod
    def _atomic_copy(source, root_fd: int, name: str) -> None:
        partial = f'.{name}.{secrets.token_hex(6)}.partial'
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        try:
            fd = os.open(partial, flags, 0o600, dir_fd=root_fd)
            with os.fdopen(fd, 'wb') as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            os.replace(partial, name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            os.fsync(root_fd)
        finally:
            try:
                os.unlink(partial, dir_fd=root_fd)
            except FileNotFoundError:
                pass

    def upload(self, path: Path, data: dict[str, Any]) -> str:
        self._validate_name(path.name)
        manifest_name = path.name + '.manifest.json'
        payload = _manifest_bytes(data)
        with self._open_root(create=True) as root_fd:
            assert root_fd is not None
            with path.open('rb') as source:
                self._atomic_copy(source, root_fd, path.name)
            remote_checksum, remote_size = self._file_checksum(root_fd, path.name)
            if remote_size != data['size'] or remote_checksum != data['sha256']:
                os.unlink(path.name, dir_fd=root_fd)
                raise BackupError('Off-server copy verification failed')
            self._atomic_copy(io.BytesIO(payload), root_fd, manifest_name)
            if self._read(root_fd, manifest_name, limit=MAX_MANIFEST_BYTES) != payload:
                os.unlink(manifest_name, dir_fd=root_fd)
                raise BackupError('Off-server manifest verification failed')
        return path.name

    def list(self) -> list[RemoteSnapshot]:
        result = []
        with self._open_root(create=False) as root_fd:
            if root_fd is None:
                return []
            for name in os.listdir(root_fd):
                if not name.endswith('.sqlite.manifest.json'):
                    continue
                try:
                    item = _parse_manifest(self._read(root_fd, name, limit=MAX_MANIFEST_BYTES))
                    if name != item.filename + '.manifest.json':
                        continue
                    digest, size = self._file_checksum(root_fd, item.filename)
                    if size == item.size and digest == item.checksum:
                        result.append(RemoteSnapshot(
                            item.filename, item.checksum, item.size, item.created_at,
                            item.filename, item.schema_version, item.application_version))
                except (FileNotFoundError, BackupError):
                    continue
        return sorted(result, key=lambda x: (x.created_at, x.filename), reverse=True)

    def download(self, item: RemoteSnapshot, target: Path) -> None:
        self._validate_name(item.filename)
        with self._open_root(create=False) as root_fd:
            if root_fd is None:
                raise BackupError('Remote snapshot was not found')
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            source_fd = os.open(item.filename, flags, dir_fd=root_fd)
            try:
                if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                    raise BackupError('Remote backup file is invalid')
                with os.fdopen(source_fd, 'rb', closefd=False) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                os.close(source_fd)

    def delete(self, item: RemoteSnapshot) -> None:
        self._validate_name(item.filename)
        with self._open_root(create=False) as root_fd:
            if root_fd is None:
                return
            for name in (item.filename + '.manifest.json', item.filename):
                try:
                    os.unlink(name, dir_fd=root_fd)
                except FileNotFoundError:
                    pass
            os.fsync(root_fd)


class S3Provider(Provider):
    def __init__(self, cfg: dict[str, Any]):
        try: import boto3
        except ImportError as exc: raise BackupError('S3 support is not installed') from exc
        access = _secret('BACKUP_S3_ACCESS_KEY_ID'); secret = _secret('BACKUP_S3_SECRET_ACCESS_KEY')
        if not access or not secret: raise BackupError('S3 credentials are not configured')
        kwargs: dict[str, Any] = {'aws_access_key_id': access, 'aws_secret_access_key': secret}
        if token := _secret('BACKUP_S3_SESSION_TOKEN'): kwargs['aws_session_token'] = token
        if cfg.get('region'): kwargs['region_name'] = cfg['region']
        if cfg.get('endpoint'): kwargs['endpoint_url'] = cfg['endpoint']
        self.client = boto3.client('s3', **kwargs)
        self.bucket, self.prefix, self.encryption = cfg['bucket'], cfg.get('prefix', ''), cfg['encryption']

    def _key(self, name: str) -> str:
        return '/'.join(part for part in (self.prefix, name) if part)

    def _list_prefix(self) -> str:
        return self.prefix.rstrip('/') + '/' if self.prefix else ''

    @staticmethod
    def _not_found(exc: BaseException) -> bool:
        code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
        return str(code) in ('404', 'NoSuchKey', 'NotFound')

    def _read_object(self, key: str, *, limit: int | None = None) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=key)['Body']
        try:
            payload = body.read() if limit is None else body.read(limit + 1)
        finally:
            close = getattr(body, 'close', None)
            if close:
                close()
        if limit is not None and len(payload) > limit:
            raise BackupError('Remote backup manifest is invalid')
        return payload

    def _verify_database(self, key: str, data: dict[str, Any]) -> None:
        body = self.client.get_object(Bucket=self.bucket, Key=key)['Body']
        try:
            digest, size = _stream_checksum(body)
        finally:
            close = getattr(body, 'close', None)
            if close:
                close()
        if size != data['size'] or digest != data['sha256']:
            raise BackupError('S3 upload verification failed')

    def probe(self) -> None: self.client.head_bucket(Bucket=self.bucket)

    def upload(self, path: Path, data: dict[str, Any]) -> str:
        extra: dict[str, Any] = {
            'Metadata': {'sha256': data['sha256']},
            'ContentType': 'application/vnd.sqlite3',
            'ChecksumAlgorithm': 'SHA256',
        }
        if self.encryption == 'sse-s3': extra['ServerSideEncryption'] = 'AES256'
        if self.encryption == 'sse-kms':
            extra['ServerSideEncryption'] = 'aws:kms'
            key = _secret('BACKUP_S3_KMS_KEY_ID')
            if key: extra['SSEKMSKeyId'] = key
        object_key = self._key(path.name)
        self.client.upload_file(str(path), self.bucket, object_key, ExtraArgs=extra)
        head = self.client.head_object(Bucket=self.bucket, Key=object_key)
        if head.get('ContentLength') != data['size'] or head.get('Metadata', {}).get('sha256') != data['sha256']:
            raise BackupError('S3 upload verification failed')
        if self.encryption == 'sse-s3' and head.get('ServerSideEncryption') != 'AES256':
            raise BackupError('S3 upload encryption verification failed')
        if self.encryption == 'sse-kms' and head.get('ServerSideEncryption') != 'aws:kms':
            raise BackupError('S3 upload encryption verification failed')
        self._verify_database(object_key, data)
        body = _manifest_bytes(data)
        manifest_extra = {'ContentType': 'application/json', 'Metadata': {'sha256': hashlib.sha256(body).hexdigest()}}
        if self.encryption == 'sse-s3': manifest_extra['ServerSideEncryption'] = 'AES256'
        if self.encryption == 'sse-kms':
            manifest_extra['ServerSideEncryption'] = 'aws:kms'
            if key := _secret('BACKUP_S3_KMS_KEY_ID'): manifest_extra['SSEKMSKeyId'] = key
        manifest_key = self._key(path.name + '.manifest.json')
        self.client.put_object(Bucket=self.bucket, Key=manifest_key, Body=body, **manifest_extra)
        if self._read_object(manifest_key, limit=MAX_MANIFEST_BYTES) != body:
            raise BackupError('S3 manifest verification failed')
        return object_key

    def list(self) -> list[RemoteSnapshot]:
        result = []
        paginator = self.client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._list_prefix()):
            for obj in page.get('Contents', []):
                key = obj.get('Key', '')
                if not key.endswith('.sqlite.manifest.json'): continue
                try:
                    payload = self._read_object(key, limit=MAX_MANIFEST_BYTES)
                    item = _parse_manifest(payload)
                    if key != self._key(item.filename + '.manifest.json'):
                        continue
                    head = self.client.head_object(Bucket=self.bucket, Key=self._key(item.filename))
                    if head.get('ContentLength') == item.size and head.get('Metadata', {}).get('sha256') == item.checksum:
                        result.append(RemoteSnapshot(item.filename, item.checksum, item.size, item.created_at,
                                                     self._key(item.filename), item.schema_version, item.application_version))
                except BackupError:
                    continue
                except Exception as exc:
                    if self._not_found(exc):
                        continue
                    raise
        return sorted(result, key=lambda x: (x.created_at, x.filename), reverse=True)

    def download(self, item: RemoteSnapshot, target: Path) -> None:
        self.client.download_file(self.bucket, self._key(item.filename), str(target))

    def delete(self, item: RemoteSnapshot) -> None:
        response = self.client.delete_objects(Bucket=self.bucket, Delete={'Objects': [
            {'Key': self._key(item.filename)}, {'Key': self._key(item.filename + '.manifest.json')}]})
        if response.get('Errors'):
            raise BackupError('S3 backup pruning failed')


class DriveProvider(Provider):
    def __init__(self):
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.exceptions import RefreshError
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
        except ImportError as exc: raise BackupError('Google Drive support is not installed') from exc
        token = token_file()
        if not token.exists() or token.is_symlink():
            raise RelinkRequired('Google Drive must be linked again')
        try:
            credentials = Credentials.from_authorized_user_file(str(token), [SCOPE])
        except (OSError, ValueError) as exc:
            raise RelinkRequired('Google Drive must be linked again') from exc
        if credentials.expired:
            if not credentials.refresh_token:
                raise RelinkRequired('Google Drive must be linked again')
            try:
                credentials.refresh(Request())
                _write_token(credentials.to_json())
            except RefreshError as exc:
                raise RelinkRequired('Google Drive must be linked again') from exc
            except Exception as exc:
                raise BackupError('Google Drive authentication is temporarily unavailable') from exc
        if not credentials.valid:
            raise RelinkRequired('Google Drive must be linked again')
        self.service = build('drive', 'v3', credentials=credentials, cache_discovery=False)
        self.folder = self._folder(create=False)

    def _folder(self, create: bool = True) -> str | None:
        query = "trashed=false and mimeType='application/vnd.google-apps.folder' and appProperties has { key='indigoStats' and value='backups-v1' }"
        found = self.service.files().list(q=query, spaces='drive', fields='files(id)', pageSize=2).execute().get('files', [])
        if found: return found[0]['id']
        if not create: return None
        body = {'name': FOLDER_NAME, 'mimeType': 'application/vnd.google-apps.folder', 'appProperties': FOLDER_PROPERTY}
        return self.service.files().create(body=body, fields='id').execute()['id']

    def probe(self) -> None: self.service.about().get(fields='storageQuota').execute()

    def _find(self, name: str, kind: str) -> list[dict[str, Any]]:
        if not self.folder: return []
        safe = name.replace('\\', '\\\\').replace("'", "\\'")
        query = f"'{self.folder}' in parents and trashed=false and name='{safe}'"
        found = self.service.files().list(
            q=query, spaces='drive', fields='files(id,name,size,sha256Checksum,appProperties)',
            pageSize=100).execute().get('files', [])
        return [item for item in found if item.get('appProperties', {}).get('indigoStatsType') == kind]

    def upload(self, path: Path, data: dict[str, Any]) -> str:
        from googleapiclient.http import MediaFileUpload
        if not self.folder: self.folder = self._folder(create=True)
        old_snapshots = self._find(path.name, 'snapshot')
        old_manifests = self._find(path.name + '.manifest.json', 'manifest')
        body = {'name': path.name, 'parents': [self.folder],
                'appProperties': {'indigoStatsType': 'snapshot'}}
        media = MediaFileUpload(str(path), mimetype='application/vnd.sqlite3', resumable=True)
        uploaded = self.service.files().create(
            body=body, media_body=media, fields='id,size,sha256Checksum').execute()
        if (int(uploaded.get('size', -1)) != data['size']
                or uploaded.get('sha256Checksum') != data['sha256']):
            if uploaded.get('id'):
                try:
                    self.service.files().delete(fileId=uploaded['id']).execute()
                except Exception:
                    pass
            raise BackupError('Google Drive upload verification failed')
        payload = _manifest_bytes(data)
        temp = db.DATA / 'backups' / f'.{path.name}.{secrets.token_hex(6)}.manifest.partial'
        manifest = None
        try:
            with temp.open('xb') as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temp, 0o600)
            mbody = {'name': path.name + '.manifest.json', 'parents': [self.folder], 'appProperties': {'indigoStatsType': 'manifest'}}
            manifest = self.service.files().create(
                body=mbody,
                media_body=MediaFileUpload(str(temp), mimetype='application/json', resumable=True),
                fields='id,size,sha256Checksum').execute()
            if (int(manifest.get('size', -1)) != len(payload)
                    or manifest.get('sha256Checksum') != hashlib.sha256(payload).hexdigest()):
                raise BackupError('Google Drive manifest verification failed')
        except Exception:
            for created in (manifest, uploaded):
                if created and created.get('id'):
                    try:
                        self.service.files().delete(fileId=created['id']).execute()
                    except Exception:
                        pass
            raise
        finally:
            temp.unlink(missing_ok=True)
        for old in old_manifests + old_snapshots:
            self.service.files().delete(fileId=old['id']).execute()
        return uploaded['id']

    def list(self) -> list[RemoteSnapshot]:
        from googleapiclient.http import MediaIoBaseDownload
        if not self.folder: return []
        query = f"'{self.folder}' in parents and trashed=false and appProperties has {{ key='indigoStatsType' and value='manifest' }}"
        files = []
        page_token = None
        while True:
            request = self.service.files().list(
                q=query, spaces='drive', fields='nextPageToken,files(id,name)',
                pageSize=1000, pageToken=page_token)
            page = request.execute()
            files.extend(page.get('files', []))
            page_token = page.get('nextPageToken')
            if not page_token:
                break
        result = []
        for remote in files:
            try:
                out = io.BytesIO(); downloader = MediaIoBaseDownload(out, self.service.files().get_media(fileId=remote['id']))
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                    if out.tell() > MAX_MANIFEST_BYTES:
                        raise BackupError('Remote backup manifest is invalid')
                item = _parse_manifest(out.getvalue())
                if remote.get('name') != item.filename + '.manifest.json':
                    continue
                data = self._find(item.filename, 'snapshot')
                match = next((x for x in data if x.get('sha256Checksum') == item.checksum
                              and x.get('size') == str(item.size)), None)
                if match: result.append(RemoteSnapshot(item.filename, item.checksum, item.size, item.created_at,
                                                       match['id'], item.schema_version, item.application_version))
            except BackupError:
                continue
            except Exception as exc:
                if _auth_error(exc): raise RelinkRequired('Google Drive must be linked again') from exc
                if getattr(getattr(exc, 'resp', None), 'status', None) == 404:
                    continue
                raise
        return sorted(result, key=lambda x: (x.created_at, x.filename), reverse=True)

    def download(self, item: RemoteSnapshot, target: Path) -> None:
        from googleapiclient.http import MediaIoBaseDownload
        with target.open('wb') as output:
            dl = MediaIoBaseDownload(output, self.service.files().get_media(fileId=item.reference)); done = False
            while not done: _, done = dl.next_chunk()

    def delete(self, item: RemoteSnapshot) -> None:
        for remote in self._find(item.filename + '.manifest.json', 'manifest') + self._find(item.filename, 'snapshot'):
            self.service.files().delete(fileId=remote['id']).execute()


def provider(cfg: dict[str, Any] | None = None) -> Provider:
    cfg = cfg or _config()
    kind = cfg.get('provider', 'disabled')
    if kind == 'filesystem': return FilesystemProvider()
    if kind == 's3': return S3Provider(cfg)
    if kind == 'google_drive':
        try: return DriveProvider()
        except Exception as exc:
            if _auth_error(exc): raise RelinkRequired('Google Drive must be linked again') from exc
            raise
    raise BackupError('Off-server backups are disabled')


def readiness(cfg: dict[str, Any]) -> dict[str, bool]:
    kind = cfg.get('provider', 'disabled')
    token = token_file()
    def present(name: str) -> bool:
        try: return bool(_secret(name))
        except BackupError: return False
    return {
        'credentials_configured': kind != 's3' or bool(present('BACKUP_S3_ACCESS_KEY_ID') and present('BACKUP_S3_SECRET_ACCESS_KEY')),
        'google_linked': token.is_file() and not token.is_symlink(),
        'oauth_configured': bool(present('BACKUP_GOOGLE_CLIENT_ID') and present('BACKUP_GOOGLE_CLIENT_SECRET') and present('BACKUP_GOOGLE_CALLBACK_URI')),
        'mount_detected': OFFSITE_ROOT.exists() and os.path.ismount(OFFSITE_ROOT) and not OFFSITE_ROOT.is_symlink(),
    }


def public_status() -> dict[str, Any]:
    cfg = _config(); kind = cfg.get('provider', 'disabled')
    with db.connect() as con:
        row = con.execute("SELECT * FROM job_status WHERE name='offsite_backup'").fetchone()
    status = dict(row) if row else {'last_attempt': None, 'last_success': None, 'error': None}
    if kind == 'disabled':
        status['last_success'] = None
    configured = {}
    if kind == 's3':
        configured = {key + '_configured': bool(cfg.get(key)) for key in ('bucket', 'prefix', 'region', 'endpoint')}
        configured['encryption'] = cfg.get('encryption', 'default')
    return {'provider': kind, 'enabled': kind != 'disabled', 'local_retention': 14,
            'remote_retention': REMOTE_RETENTION, **readiness(cfg), **configured, **status}


def probe() -> None:
    try: provider().probe()
    except Exception as exc:
        if _auth_error(exc): raise RelinkRequired('Google Drive must be linked again') from exc
        raise


def _record_attempt(dest: str, item: dict[str, Any], reference: str | None = None,
                    error: str | None = None, complete: bool = False) -> None:
    now = int(time.time())
    with db.connect() as con:
        con.execute('''INSERT INTO backup_transfers(destination,snapshot,checksum,size,remote_reference,attempts,last_attempt,completed_at,error)
            VALUES(?,?,?,?,?,1,?,?,?) ON CONFLICT(destination,snapshot) DO UPDATE SET
            checksum=excluded.checksum,size=excluded.size,
            remote_reference=CASE
                WHEN excluded.error IS NOT NULL
                    OR backup_transfers.checksum<>excluded.checksum
                    OR backup_transfers.size<>excluded.size
                    THEN excluded.remote_reference
                ELSE COALESCE(excluded.remote_reference,backup_transfers.remote_reference) END,
            attempts=backup_transfers.attempts+1,last_attempt=excluded.last_attempt,
            completed_at=CASE
                WHEN excluded.error IS NOT NULL
                    OR backup_transfers.checksum<>excluded.checksum
                    OR backup_transfers.size<>excluded.size
                    THEN excluded.completed_at
                ELSE COALESCE(excluded.completed_at,backup_transfers.completed_at) END,
            error=excluded.error''',
            (dest, item['filename'], item['sha256'], item['size'], reference, now, now if complete else None, error))


def _execute() -> None:
    try:
        cfg = _config()
        if cfg.get('provider') == 'disabled':
            db.status('offsite_backup')
            return
        with db.BACKUP_LOCK:
            dest = fingerprint(cfg)
            remote = provider(cfg)
            remote.probe()
            complete = {item.filename: item for item in remote.list()}
            local = sorted((db.DATA / 'backups').glob('*.sqlite'), reverse=True)
            for path in local:
                data = manifest(path)
                existing = complete.get(path.name)
                if existing and existing.checksum == data['sha256'] and existing.size == data['size']:
                    continue
                try:
                    reference = remote.upload(path, data)
                    _record_attempt(dest, data, reference=reference, complete=True)
                    complete[path.name] = RemoteSnapshot(
                        path.name, data['sha256'], data['size'], data['created_at'], reference)
                except Exception as exc:
                    _record_attempt(dest, data, error=_safe_error(exc))
                    raise
            current = sorted(complete.values(), key=lambda x: (x.created_at, x.filename), reverse=True)
            for item in current[REMOTE_RETENTION:]:
                remote.delete(item)
        db.status('offsite_backup', success=True)
    except Exception as exc:
        db.status('offsite_backup', _safe_error(exc))
        raise

def run(*, wait: bool = False) -> bool:
    if not _run_lock.acquire(blocking=wait): return False
    try:
        _execute()
        return True
    finally: _run_lock.release()


def start_async() -> bool:
    if not _run_lock.acquire(blocking=False): return False
    def work():
        try: _execute()
        except Exception: pass
        finally: _run_lock.release()
    threading.Thread(target=work, name='offsite-backup', daemon=True).start()
    return True


def remote_list(cfg: dict[str, Any] | None = None) -> list[RemoteSnapshot]:
    return provider(validate_config(cfg) if cfg is not None else None).list()


def fetch(filename: str, cfg: dict[str, Any] | None = None) -> Path:
    if not SNAPSHOT_RE.fullmatch(filename): raise BackupError('Invalid snapshot filename')
    remote = provider(validate_config(cfg) if cfg is not None else None)
    item = next((x for x in remote.list() if x.filename == filename), None)
    if not item: raise BackupError('Remote snapshot was not found')
    recovery = db.DATA / 'recovery'
    if recovery.is_symlink():
        raise BackupError('Recovery directory is unsafe')
    recovery.mkdir(mode=0o700, exist_ok=True)
    if recovery.is_symlink() or not recovery.is_dir():
        raise BackupError('Recovery directory is unsafe')
    target = recovery / filename
    manifest_target = recovery / (filename + '.manifest.json')
    if target.exists() or manifest_target.exists():
        raise BackupError('Recovery file already exists')
    suffix = secrets.token_hex(6)
    partial = recovery / f'.{filename}.{suffix}.partial'
    manifest_partial = recovery / f'.{filename}.{suffix}.manifest.partial'
    published_database = False
    published_manifest = False
    try:
        remote.download(item, partial)
        if partial.stat().st_size != item.size or checksum(partial) != item.checksum:
            raise BackupError('Downloaded snapshot checksum verification failed')
        with closing(sqlite3.connect(partial.as_uri() + '?mode=ro&immutable=1', uri=True)) as con:
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError('Downloaded snapshot integrity check failed')
            schema = con.execute('PRAGMA user_version').fetchone()[0]
        os.chmod(partial, 0o600)
        recovered_manifest = {'format': 1, 'filename': item.filename, 'size': item.size,
            'created_at': item.created_at, 'schema_version': item.schema_version or schema,
            'application_version': item.application_version or 'remote', 'sha256': item.checksum}
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        manifest_fd = os.open(manifest_partial, flags, 0o600)
        with os.fdopen(manifest_fd, 'w', encoding='utf-8') as output:
            json.dump(recovered_manifest, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.link(partial, target, follow_symlinks=False)
        published_database = True
        os.link(manifest_partial, manifest_target, follow_symlinks=False)
        published_manifest = True
        directory = os.open(recovery, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return target
    except FileExistsError as exc:
        raise BackupError('Recovery file already exists') from exc
    finally:
        partial.unlink(missing_ok=True)
        manifest_partial.unlink(missing_ok=True)
        if published_database and not published_manifest:
            target.unlink(missing_ok=True)


def _google_flow():
    try: from google_auth_oauthlib.flow import Flow
    except ImportError as exc: raise BackupError('Google Drive support is not installed') from exc
    client_id, client_secret, callback = (_secret('BACKUP_GOOGLE_CLIENT_ID'), _secret('BACKUP_GOOGLE_CLIENT_SECRET'), _secret('BACKUP_GOOGLE_CALLBACK_URI'))
    if not all((client_id, client_secret, callback)): raise BackupError('Google OAuth secrets are not configured')
    parsed = urlparse(callback)
    loopback = parsed.hostname in ('localhost', '127.0.0.1', '::1')
    if (not parsed.hostname or parsed.username or parsed.password or parsed.fragment
            or not (parsed.scheme == 'https' or (parsed.scheme == 'http' and loopback))):
        raise BackupError('Google OAuth callback must use HTTPS')
    flow = Flow.from_client_config({'web': {'client_id': client_id, 'client_secret': client_secret,
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': 'https://oauth2.googleapis.com/token',
        'redirect_uris': [callback]}}, scopes=[SCOPE], redirect_uri=callback)
    return flow


def google_authorization_url() -> str:
    flow = _google_flow()
    now = time.time()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b'=').decode()
    state = secrets.token_urlsafe(32)
    with _oauth_lock:
        for expired in [key for key, value in _oauth_states.items() if value[1] < now]:
            _oauth_states.pop(expired, None)
        while len(_oauth_states) >= MAX_OAUTH_STATES:
            oldest = min(_oauth_states, key=lambda key: _oauth_states[key][1])
            _oauth_states.pop(oldest)
        _oauth_states[state] = (verifier, now + 600)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    url, _ = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent',
                                     state=state, code_challenge=challenge,
                                     code_challenge_method='S256')
    return url


def google_callback(code: str, state: str) -> None:
    with _oauth_lock:
        saved = _oauth_states.pop(state, None)
    if not saved or saved[1] < time.time(): raise BackupError('Google authorization session expired')
    flow = _google_flow()
    flow.fetch_token(code=code, code_verifier=saved[0])
    _write_token(flow.credentials.to_json())


def _write_token(payload: str) -> None:
    token = token_file()
    if token.parent.is_symlink():
        raise BackupError('Google Drive token directory is unsafe')
    token.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if token.parent.is_symlink() or not token.parent.is_dir():
        raise BackupError('Google Drive token directory is unsafe')
    os.chmod(token.parent, 0o700)
    temp = token.with_name(f'.{token.name}.{secrets.token_hex(6)}.partial')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(temp, flags, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, token)
        os.chmod(token, stat.S_IRUSR | stat.S_IWUSR)
        directory = os.open(token.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def google_unlink() -> None:
    if not _run_lock.acquire(blocking=False):
        raise BackupBusy('An off-server backup is already running')
    try:
        token_file().unlink(missing_ok=True)
    finally:
        _run_lock.release()
