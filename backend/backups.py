"""Verified off-server copies of consistent local SQLite snapshots."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from . import db

OFFSITE_ROOT = Path('/offsite')
REMOTE_RETENTION = 30
SCOPE = 'https://www.googleapis.com/auth/drive.file'
FOLDER_NAME = 'Indigo Stats Backups'
FOLDER_PROPERTY = {'indigoStats': 'backups-v1'}
SNAPSHOT_RE = re.compile(r'^\d{8}T\d{6}Z\.sqlite$')
_run_lock = threading.Lock()
_oauth_states: dict[str, tuple[str, float]] = {}

def token_file() -> Path:
    return db.DATA / 'secrets' / 'google-drive-token.json'


class BackupError(RuntimeError):
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


def validate_config(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError('Backup configuration must be an object')
    provider = value.get('provider')
    allowed = {
        'disabled': {'provider'},
        'filesystem': {'provider'},
        's3': {'provider', 'bucket', 'prefix', 'region', 'endpoint', 'encryption'},
        'google_drive': {'provider'},
    }
    if provider not in allowed or set(value) - allowed[provider]:
        raise ValueError('Invalid backup provider configuration')
    if provider == 'disabled':
        return {'provider': 'disabled'}
    if provider == 'filesystem':
        return {'provider': 'filesystem'}
    if provider == 'google_drive':
        return {'provider': 'google_drive'}
    bucket = str(value.get('bucket', '')).strip()
    if not bucket or len(bucket) > 255:
        raise ValueError('S3 bucket is required')
    prefix = str(value.get('prefix', 'indigo-stats')).strip().strip('/')
    if '..' in PurePosixPath(prefix).parts or len(prefix) > 512:
        raise ValueError('Invalid S3 prefix')
    region = str(value.get('region', '')).strip()
    endpoint = str(value.get('endpoint', '')).strip()
    if endpoint:
        parsed = urlparse(endpoint)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError('Custom S3 endpoint must be HTTPS')
        endpoint = endpoint.rstrip('/')
    encryption = str(value.get('encryption', 'default'))
    if encryption not in ('default', 'sse-s3', 'sse-kms'):
        raise ValueError('Invalid S3 encryption mode')
    return {'provider': 's3', 'bucket': bucket, 'prefix': prefix, 'region': region,
            'endpoint': endpoint, 'encryption': encryption}


def set_config(value: dict[str, Any]) -> dict[str, Any]:
    clean = validate_config(value)
    db.set_settings({'offsite_backup': clean})
    return clean


def fingerprint(cfg: dict[str, Any]) -> str:
    stable = json.dumps(cfg, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(stable).hexdigest()


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(path)) as con:
        schema = con.execute('PRAGMA user_version').fetchone()[0]
    from .app import APP_VERSION
    return {'format': 1, 'filename': path.name, 'size': path.stat().st_size,
            'created_at': int(path.stat().st_mtime), 'schema_version': schema,
            'application_version': APP_VERSION, 'sha256': checksum(path)}


def _parse_manifest(payload: bytes | str) -> RemoteSnapshot:
    try:
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

    def _path(self, name: str) -> Path:
        if name not in (Path(name).name,) or not (SNAPSHOT_RE.fullmatch(name) or name.endswith('.sqlite.manifest.json')):
            raise BackupError('Invalid remote backup name')
        path = self.root / name
        if self.root.exists() and self.root.is_symlink():
            raise BackupError('The off-server backup directory must not be a symlink')
        return path

    def probe(self) -> None:
        self._validate_mount()
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise BackupError('The off-server backup directory is unsafe')
        if self.root.exists() and not os.access(self.root, os.W_OK | os.X_OK):
            raise BackupError('The off-server backup directory is not writable')

    def _atomic_copy(self, source: Path, target: Path) -> None:
        partial = target.with_name('.' + target.name + '.' + secrets.token_hex(6) + '.partial')
        try:
            with source.open('rb') as src, partial.open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
                dst.flush(); os.fsync(dst.fileno())
            os.chmod(partial, 0o600)
            os.replace(partial, target)
            directory = os.open(target.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            partial.unlink(missing_ok=True)

    def upload(self, path: Path, data: dict[str, Any]) -> str:
        self._validate_mount()
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink(): raise BackupError('The off-server backup directory is unsafe')
        target = self._path(path.name)
        self._atomic_copy(path, target)
        if target.stat().st_size != data['size'] or checksum(target) != data['sha256']:
            target.unlink(missing_ok=True)
            raise BackupError('Off-server copy verification failed')
        manifest_path = self._path(path.name + '.manifest.json')
        temp = db.DATA / 'backups' / (path.name + '.manifest.tmp')
        temp.write_text(json.dumps(data, sort_keys=True, separators=(',', ':')))
        try: self._atomic_copy(temp, manifest_path)
        finally: temp.unlink(missing_ok=True)
        return path.name

    def list(self) -> list[RemoteSnapshot]:
        self.probe()
        if not self.root.exists(): return []
        result = []
        for path in self.root.glob('*.sqlite.manifest.json'):
            if path.is_symlink() or not path.is_file(): continue
            try:
                item = _parse_manifest(path.read_bytes())
                data = self._path(item.filename)
                if (data.is_file() and not data.is_symlink() and data.stat().st_size == item.size
                        and checksum(data) == item.checksum):
                    result.append(RemoteSnapshot(item.filename, item.checksum, item.size, item.created_at,
                                                 item.filename, item.schema_version, item.application_version))
            except (OSError, BackupError):
                continue
        return sorted(result, key=lambda x: (x.created_at, x.filename), reverse=True)

    def download(self, item: RemoteSnapshot, target: Path) -> None:
        self.probe(); source = self._path(item.filename)
        self._atomic_copy(source, target)

    def delete(self, item: RemoteSnapshot) -> None:
        self.probe()
        self._path(item.filename + '.manifest.json').unlink(missing_ok=True)
        self._path(item.filename).unlink(missing_ok=True)


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

    def probe(self) -> None: self.client.head_bucket(Bucket=self.bucket)

    def upload(self, path: Path, data: dict[str, Any]) -> str:
        extra: dict[str, Any] = {'Metadata': {'sha256': data['sha256']}, 'ContentType': 'application/vnd.sqlite3'}
        if self.encryption == 'sse-s3': extra['ServerSideEncryption'] = 'AES256'
        if self.encryption == 'sse-kms':
            extra['ServerSideEncryption'] = 'aws:kms'
            key = _secret('BACKUP_S3_KMS_KEY_ID')
            if key: extra['SSEKMSKeyId'] = key
        self.client.upload_file(str(path), self.bucket, self._key(path.name), ExtraArgs=extra)
        head = self.client.head_object(Bucket=self.bucket, Key=self._key(path.name))
        if head.get('ContentLength') != data['size'] or head.get('Metadata', {}).get('sha256') != data['sha256']:
            raise BackupError('S3 upload verification failed')
        body = json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
        manifest_extra = {'ContentType': 'application/json', 'Metadata': {'sha256': hashlib.sha256(body).hexdigest()}}
        if self.encryption == 'sse-s3': manifest_extra['ServerSideEncryption'] = 'AES256'
        if self.encryption == 'sse-kms':
            manifest_extra['ServerSideEncryption'] = 'aws:kms'
            if key := _secret('BACKUP_S3_KMS_KEY_ID'): manifest_extra['SSEKMSKeyId'] = key
        self.client.put_object(Bucket=self.bucket, Key=self._key(path.name + '.manifest.json'), Body=body, **manifest_extra)
        return self._key(path.name)

    def list(self) -> list[RemoteSnapshot]:
        result = []
        paginator = self.client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._key('')):
            for obj in page.get('Contents', []):
                key = obj.get('Key', '')
                if not key.endswith('.sqlite.manifest.json'): continue
                try:
                    payload = self.client.get_object(Bucket=self.bucket, Key=key)['Body'].read()
                    item = _parse_manifest(payload)
                    head = self.client.head_object(Bucket=self.bucket, Key=self._key(item.filename))
                    if head.get('ContentLength') == item.size and head.get('Metadata', {}).get('sha256') == item.checksum:
                        result.append(RemoteSnapshot(item.filename, item.checksum, item.size, item.created_at,
                                                     self._key(item.filename), item.schema_version, item.application_version))
                except Exception:
                    continue
        return sorted(result, key=lambda x: (x.created_at, x.filename), reverse=True)

    def download(self, item: RemoteSnapshot, target: Path) -> None:
        self.client.download_file(self.bucket, self._key(item.filename), str(target))

    def delete(self, item: RemoteSnapshot) -> None:
        self.client.delete_objects(Bucket=self.bucket, Delete={'Objects': [
            {'Key': self._key(item.filename)}, {'Key': self._key(item.filename + '.manifest.json')}]})


class DriveProvider(Provider):
    def __init__(self, cfg: dict[str, Any] | None = None):
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
        except ImportError as exc: raise BackupError('Google Drive support is not installed') from exc
        token = token_file()
        if not token.exists(): raise RelinkRequired('Google Drive must be linked again')
        try:
            credentials = Credentials.from_authorized_user_file(str(token), [SCOPE])
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request()); _write_token(credentials.to_json())
            if not credentials.valid: raise RelinkRequired('Google Drive must be linked again')
        except RelinkRequired: raise
        except Exception as exc: raise RelinkRequired('Google Drive must be linked again') from exc
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
        found = self.service.files().list(q=query, spaces='drive', fields='files(id,name,appProperties)', pageSize=10).execute().get('files', [])
        return [item for item in found if item.get('appProperties', {}).get('indigoStatsType') == kind]

    def upload(self, path: Path, data: dict[str, Any]) -> str:
        from googleapiclient.http import MediaFileUpload
        if not self.folder: self.folder = self._folder(create=True)
        for old in self._find(path.name, 'snapshot'): self.service.files().delete(fileId=old['id']).execute()
        body = {'name': path.name, 'parents': [self.folder], 'appProperties': {'indigoStatsType': 'snapshot', 'sha256': data['sha256'], 'size': str(data['size'])}}
        media = MediaFileUpload(str(path), mimetype='application/vnd.sqlite3', resumable=True)
        uploaded = self.service.files().create(body=body, media_body=media, fields='id,size,appProperties').execute()
        if int(uploaded.get('size', -1)) != data['size'] or uploaded.get('appProperties', {}).get('sha256') != data['sha256']:
            raise BackupError('Google Drive upload verification failed')
        payload = json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
        temp = db.DATA / 'backups' / (path.name + '.manifest.tmp'); temp.write_bytes(payload)
        try:
            for old in self._find(path.name + '.manifest.json', 'manifest'): self.service.files().delete(fileId=old['id']).execute()
            mbody = {'name': path.name + '.manifest.json', 'parents': [self.folder], 'appProperties': {'indigoStatsType': 'manifest'}}
            self.service.files().create(body=mbody, media_body=MediaFileUpload(str(temp), mimetype='application/json', resumable=True), fields='id').execute()
        finally: temp.unlink(missing_ok=True)
        return uploaded['id']

    def list(self) -> list[RemoteSnapshot]:
        from googleapiclient.http import MediaIoBaseDownload
        import io
        if not self.folder: return []
        query = f"'{self.folder}' in parents and trashed=false and appProperties has {{ key='indigoStatsType' and value='manifest' }}"
        files = self.service.files().list(q=query, spaces='drive', fields='files(id,name)', pageSize=1000).execute().get('files', [])
        result = []
        for remote in files:
            try:
                out = io.BytesIO(); downloader = MediaIoBaseDownload(out, self.service.files().get_media(fileId=remote['id']))
                done = False
                while not done: _, done = downloader.next_chunk()
                item = _parse_manifest(out.getvalue()); data = self._find(item.filename, 'snapshot')
                match = next((x for x in data if x.get('appProperties', {}).get('sha256') == item.checksum
                              and x.get('appProperties', {}).get('size') == str(item.size)), None)
                if match: result.append(RemoteSnapshot(item.filename, item.checksum, item.size, item.created_at,
                                                       match['id'], item.schema_version, item.application_version))
            except Exception as exc:
                if _auth_error(exc): raise RelinkRequired('Google Drive must be linked again') from exc
                continue
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
        try: return DriveProvider(cfg)
        except Exception as exc:
            if _auth_error(exc): raise RelinkRequired('Google Drive must be linked again') from exc
            raise
    raise BackupError('Off-server backups are disabled')


def readiness(cfg: dict[str, Any]) -> dict[str, bool]:
    kind = cfg.get('provider', 'disabled')
    def present(name: str) -> bool:
        try: return bool(_secret(name))
        except BackupError: return False
    return {
        'credentials_configured': kind != 's3' or bool(present('BACKUP_S3_ACCESS_KEY_ID') and present('BACKUP_S3_SECRET_ACCESS_KEY')),
        'google_linked': token_file().exists(),
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
            checksum=excluded.checksum,size=excluded.size,remote_reference=COALESCE(excluded.remote_reference,backup_transfers.remote_reference),
            attempts=backup_transfers.attempts+1,last_attempt=excluded.last_attempt,
            completed_at=COALESCE(excluded.completed_at,backup_transfers.completed_at),error=excluded.error''',
            (dest, item['filename'], item['sha256'], item['size'], reference, now, now if complete else None, error))


def _execute() -> None:
    try:
        cfg = _config()
        if cfg.get('provider') == 'disabled':
            return
        dest = fingerprint(cfg); remote = provider(cfg); remote.probe()
        complete = {item.filename: item for item in remote.list()}; uploaded = False
        local = sorted((db.DATA / 'backups').glob('*.sqlite'), reverse=True)
        for path in local:
            data = manifest(path); existing = complete.get(path.name)
            if existing and existing.checksum == data['sha256'] and existing.size == data['size']:
                continue
            try:
                reference = remote.upload(path, data)
                _record_attempt(dest, data, reference=reference, complete=True)
                complete[path.name] = RemoteSnapshot(path.name, data['sha256'], data['size'], data['created_at'], reference)
                uploaded = True
            except Exception as exc:
                _record_attempt(dest, data, error=_safe_error(exc)); raise
        if uploaded:
            current = sorted(complete.values(), key=lambda x: (x.created_at, x.filename), reverse=True)
            for item in current[REMOTE_RETENTION:]: remote.delete(item)
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
    recovery = db.DATA / 'recovery'; recovery.mkdir(mode=0o700, exist_ok=True)
    target = recovery / filename; manifest_target = recovery / (filename + '.manifest.json')
    if target.exists() or manifest_target.exists(): raise BackupError('Recovery file already exists')
    partial = target.with_suffix('.partial')
    try:
        remote.download(item, partial)
        if partial.stat().st_size != item.size or checksum(partial) != item.checksum:
            raise BackupError('Downloaded snapshot checksum verification failed')
        with closing(sqlite3.connect(partial)) as con:
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError('Downloaded snapshot integrity check failed')
            schema = con.execute('PRAGMA user_version').fetchone()[0]
        os.chmod(partial, 0o600); os.replace(partial, target)
        recovered_manifest = {'format': 1, 'filename': item.filename, 'size': item.size,
            'created_at': item.created_at, 'schema_version': item.schema_version or schema,
            'application_version': item.application_version or 'remote', 'sha256': item.checksum}
        manifest_target.write_text(json.dumps(recovered_manifest, sort_keys=True)); os.chmod(manifest_target, 0o600)
        return target
    finally: partial.unlink(missing_ok=True)


def google_authorization_url() -> str:
    try: from google_auth_oauthlib.flow import Flow
    except ImportError as exc: raise BackupError('Google Drive support is not installed') from exc
    client_id, client_secret, callback = (_secret('BACKUP_GOOGLE_CLIENT_ID'), _secret('BACKUP_GOOGLE_CLIENT_SECRET'), _secret('BACKUP_GOOGLE_CALLBACK_URI'))
    if not all((client_id, client_secret, callback)): raise BackupError('Google OAuth secrets are not configured')
    if not (callback.startswith('https://') or callback.startswith('http://localhost')):
        raise BackupError('Google OAuth callback must use HTTPS')
    now = time.time()
    for expired in [k for k, v in _oauth_states.items() if v[1] < now]:
        _oauth_states.pop(expired, None)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b'=').decode()
    state = secrets.token_urlsafe(32); _oauth_states[state] = (verifier, now + 600)
    flow = Flow.from_client_config({'web': {'client_id': client_id, 'client_secret': client_secret,
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': 'https://oauth2.googleapis.com/token',
        'redirect_uris': [callback]}}, scopes=[SCOPE], redirect_uri=callback)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    url, _ = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent',
                                     state=state, code_challenge=challenge,
                                     code_challenge_method='S256')
    return url


def google_callback(code: str, state: str) -> None:
    try: from google_auth_oauthlib.flow import Flow
    except ImportError as exc: raise BackupError('Google Drive support is not installed') from exc
    saved = _oauth_states.pop(state, None)
    if not saved or saved[1] < time.time(): raise BackupError('Google authorization session expired')
    client_id, client_secret, callback = (_secret('BACKUP_GOOGLE_CLIENT_ID'), _secret('BACKUP_GOOGLE_CLIENT_SECRET'), _secret('BACKUP_GOOGLE_CALLBACK_URI'))
    flow = Flow.from_client_config({'web': {'client_id': client_id, 'client_secret': client_secret,
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': 'https://oauth2.googleapis.com/token',
        'redirect_uris': [callback]}}, scopes=[SCOPE], redirect_uri=callback)
    flow.fetch_token(code=code, code_verifier=saved[0]); _write_token(flow.credentials.to_json())


def _write_token(payload: str) -> None:
    token = token_file()
    token.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(token.parent, 0o700)
    temp = token.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output: output.write(payload); output.flush(); os.fsync(output.fileno())
    os.replace(temp, token); os.chmod(token, stat.S_IRUSR | stat.S_IWUSR)


def google_unlink() -> None:
    token_file().unlink(missing_ok=True)
