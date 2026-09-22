import errno
import hashlib
import io
import json
import os
import stat
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from google.auth.exceptions import RefreshError

from backend import backups, db


class BackupHardeningTests(unittest.TestCase):
    def setUp(self):
        self.data_temp = tempfile.TemporaryDirectory()
        self.offsite_temp = tempfile.TemporaryDirectory()
        self.data_patch = patch.object(db, 'DATA', Path(self.data_temp.name))
        self.data_patch.start()
        self.offsite = Path(self.offsite_temp.name)
        db.initialize()

    def tearDown(self):
        backups._oauth_states.clear()
        self.data_patch.stop()
        self.offsite_temp.cleanup()
        self.data_temp.cleanup()

    def mounted(self, path):
        return Path(path) == self.offsite

    def local_snapshot(self):
        path = db.backup()
        data = backups.manifest(path)
        item = backups.RemoteSnapshot(
            path.name, data['sha256'], data['size'], data['created_at'], 'remote-id',
            data['schema_version'], data['application_version'],
        )
        return path, data, item

    def s3_provider(self, encryption='default'):
        provider = object.__new__(backups.S3Provider)
        provider.client = Mock()
        provider.bucket = 'bucket'
        provider.prefix = 'prefix'
        provider.encryption = encryption
        return provider

    def test_configuration_and_manifest_bounds(self):
        with self.assertRaisesRegex(ValueError, 'region'):
            backups.validate_config({'provider': 's3', 'bucket': 'b', 'region': 'r' * 129})
        with self.assertRaisesRegex(ValueError, 'endpoint'):
            backups.validate_config({'provider': 's3', 'bucket': 'b', 'endpoint': 'x' * 2049})
        with self.assertRaises(backups.BackupError):
            backups._parse_manifest(b'x' * (backups.MAX_MANIFEST_BYTES + 1))

    def test_filesystem_provider_rejects_unsafe_remote_shapes(self):
        provider = backups.FilesystemProvider(self.offsite)
        _, data, item = self.local_snapshot()
        with patch('os.path.ismount', side_effect=self.mounted):
            self.assertEqual(provider.list(), [])
            with self.assertRaisesRegex(backups.BackupError, 'not found'):
                provider.download(item, db.DATA / 'missing.sqlite')
            provider.delete(item)

            provider.root.mkdir()
            with provider._open_root(create=True) as root_fd:
                self.assertIsNotNone(root_fd)
                large_fd = os.open(
                    'large.manifest', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                    dir_fd=root_fd,
                )
                with os.fdopen(large_fd, 'wb') as output:
                    output.write(b'too large')
                fake_stat = types.SimpleNamespace(st_mode=stat.S_IFREG, st_size=0)
                with patch('backend.backups.os.fstat', return_value=fake_stat):
                    with self.assertRaises(backups.BackupError):
                        provider._read(root_fd, 'large.manifest', limit=1)
                os.mkdir('directory', dir_fd=root_fd)
                with self.assertRaises(backups.BackupError):
                    provider._read(root_fd, 'directory')
                with self.assertRaises(backups.BackupError):
                    provider._file_checksum(root_fd, 'directory')

                unrelated_fd = os.open(
                    'unrelated.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd,
                )
                os.close(unrelated_fd)
                mismatch = data | {'filename': '20990101T000001Z.sqlite'}
                manifest_fd = os.open(
                    '20990101T000000Z.sqlite.manifest.json',
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd,
                )
                with os.fdopen(manifest_fd, 'wb') as output:
                    output.write(backups._manifest_bytes(mismatch))

            self.assertEqual(provider.list(), [])

            directory_snapshot = provider.root / item.filename
            directory_snapshot.mkdir()
            with self.assertRaises(backups.BackupError):
                provider.download(item, db.DATA / 'invalid.sqlite')
            directory_snapshot.rmdir()

            provider.delete(item)

    def test_filesystem_manifest_readback_and_unexpected_open_errors(self):
        provider = backups.FilesystemProvider(self.offsite)
        path, data, _ = self.local_snapshot()
        with patch('os.path.ismount', side_effect=self.mounted), patch.object(
                provider, '_read', return_value=b'changed'):
            with self.assertRaisesRegex(backups.BackupError, 'manifest verification'):
                provider.upload(path, data)
        self.assertFalse((provider.root / (path.name + '.manifest.json')).exists())

        real_open = os.open
        with patch('os.path.ismount', side_effect=self.mounted), patch.object(
                provider, '_validate_mount'), patch('os.open') as mocked_open:
            mount_fd = real_open(self.offsite, os.O_RDONLY)
            mocked_open.side_effect = [mount_fd, OSError(errno.EIO, 'failure')]
            with self.assertRaises(OSError):
                provider._open_root(create=False).__enter__()

        with patch('os.path.ismount', side_effect=self.mounted), patch.object(
                provider, '_validate_mount'), patch('os.open') as mocked_open:
            mount_fd = real_open(self.offsite, os.O_RDONLY)
            mocked_open.side_effect = [mount_fd, FileNotFoundError]
            with self.assertRaises(FileNotFoundError):
                provider._open_root(create=True).__enter__()

    def test_s3_rejects_encryption_manifest_and_listing_failures(self):
        path, data, _ = self.local_snapshot()
        payload = path.read_bytes()
        for encryption in ('sse-s3', 'sse-kms'):
            provider = self.s3_provider(encryption)
            provider.client.head_object.return_value = {
                'ContentLength': data['size'], 'Metadata': {'sha256': data['sha256']},
            }
            with self.assertRaisesRegex(backups.BackupError, 'encryption verification'):
                provider.upload(path, data)

        provider = self.s3_provider()
        provider.client.head_object.return_value = {
            'ContentLength': data['size'], 'Metadata': {'sha256': data['sha256']},
        }
        provider.client.get_object.side_effect = [
            {'Body': io.BytesIO(payload)},
            {'Body': io.BytesIO(b'changed manifest')},
        ]
        with self.assertRaisesRegex(backups.BackupError, 'manifest verification'):
            provider.upload(path, data)

        provider.client.get_object.side_effect = None
        provider.client.get_object.return_value = {
            'Body': io.BytesIO(b'x' * (backups.MAX_MANIFEST_BYTES + 1)),
        }
        with self.assertRaises(backups.BackupError):
            provider._read_object('large', limit=backups.MAX_MANIFEST_BYTES)

        manifest = backups._manifest_bytes(data)
        wrong_key = 'other/' + path.name + '.manifest.json'
        invalid_key = 'prefix/20000101T000000Z.sqlite.manifest.json'
        missing_key = provider._key(path.name + '.manifest.json')
        provider.client.get_paginator.return_value.paginate.return_value = [{
            'Contents': [{'Key': wrong_key}, {'Key': invalid_key}, {'Key': missing_key}],
        }]

        def get_object(**kwargs):
            body = b'{}' if kwargs['Key'] == invalid_key else manifest
            return {'Body': io.BytesIO(body)}

        not_found = RuntimeError('missing')
        not_found.response = {'Error': {'Code': 'NoSuchKey'}}
        provider.client.get_object.side_effect = get_object
        provider.client.head_object.side_effect = not_found
        self.assertEqual(provider.list(), [])

    def test_drive_authentication_failure_modes_are_sanitized(self):
        with self.assertRaises(backups.RelinkRequired):
            backups.DriveProvider()

        token = backups.token_file()
        token.parent.mkdir(parents=True)
        token.write_text('{}', encoding='utf-8')

        scenarios = [
            (types.SimpleNamespace(expired=True, refresh_token=None, valid=False), backups.RelinkRequired),
            (types.SimpleNamespace(
                expired=True, refresh_token='token', valid=False,
                refresh=Mock(side_effect=RefreshError('revoked')),
            ), backups.RelinkRequired),
            (types.SimpleNamespace(
                expired=True, refresh_token='token', valid=False,
                refresh=Mock(side_effect=RuntimeError('network')),
            ), backups.BackupError),
            (types.SimpleNamespace(expired=False, refresh_token='token', valid=False), backups.RelinkRequired),
        ]
        for credentials, expected in scenarios:
            with self.subTest(expected=expected.__name__), patch(
                    'google.oauth2.credentials.Credentials.from_authorized_user_file',
                    return_value=credentials):
                with self.assertRaises(expected):
                    backups.DriveProvider()

    def test_drive_cleans_failed_uploads_and_replaces_only_after_verification(self):
        path, data, _ = self.local_snapshot()
        provider = object.__new__(backups.DriveProvider)
        provider.folder = 'folder'
        provider.service = Mock()
        files = provider.service.files.return_value

        provider._find = Mock(return_value=[])
        files.create.return_value.execute.return_value = {'id': 'bad', 'size': '0'}
        files.delete.return_value.execute.side_effect = RuntimeError('cleanup failed')
        with self.assertRaisesRegex(backups.BackupError, 'upload verification'):
            provider.upload(path, data)

        files.reset_mock()
        provider._find = Mock(side_effect=[
            [{'id': 'old-snapshot'}],
            [{'id': 'old-manifest'}],
        ])
        manifest_payload = backups._manifest_bytes(data)
        files.create.return_value.execute.side_effect = [
            {'id': 'new-snapshot', 'size': str(data['size']), 'sha256Checksum': data['sha256']},
            {'id': 'new-manifest', 'size': '0', 'sha256Checksum': 'wrong'},
        ]
        files.delete.return_value.execute.side_effect = RuntimeError('cleanup failed')
        with self.assertRaisesRegex(backups.BackupError, 'manifest verification'):
            provider.upload(path, data)

        files.reset_mock()
        provider._find = Mock(side_effect=[
            [{'id': 'old-snapshot'}],
            [{'id': 'old-manifest'}],
        ])
        files.create.return_value.execute.side_effect = [
            {'id': 'new-snapshot', 'size': str(data['size']), 'sha256Checksum': data['sha256']},
            {
                'id': 'new-manifest', 'size': str(len(manifest_payload)),
                'sha256Checksum': hashlib.sha256(manifest_payload).hexdigest(),
            },
        ]
        files.delete.return_value.execute.side_effect = None
        self.assertEqual(provider.upload(path, data), 'new-snapshot')
        deleted = [call.kwargs['fileId'] for call in files.delete.call_args_list]
        self.assertEqual(deleted, ['old-manifest', 'old-snapshot'])

    def test_drive_listing_skips_oversized_mismatched_and_missing_manifests(self):
        _, data, _ = self.local_snapshot()
        provider = object.__new__(backups.DriveProvider)
        provider.folder = 'folder'
        provider.service = Mock()
        files = provider.service.files.return_value

        files.list.return_value.execute.return_value = {
            'files': [{'id': 'large', 'name': data['filename'] + '.manifest.json'}],
        }
        with patch('googleapiclient.http.MediaIoBaseDownload') as downloader:
            def oversized(output, request):
                output.write(b'x' * (backups.MAX_MANIFEST_BYTES + 1))
                result = Mock()
                result.next_chunk.return_value = (None, True)
                return result
            downloader.side_effect = oversized
            self.assertEqual(provider.list(), [])

        files.list.return_value.execute.return_value = {
            'files': [{'id': 'mismatch', 'name': '20000101T000000Z.sqlite.manifest.json'}],
        }
        manifest = backups._manifest_bytes(data)
        with patch('googleapiclient.http.MediaIoBaseDownload') as downloader:
            def valid(output, request):
                output.write(manifest)
                result = Mock()
                result.next_chunk.return_value = (None, True)
                return result
            downloader.side_effect = valid
            self.assertEqual(provider.list(), [])

        missing = RuntimeError('missing')
        missing.resp = types.SimpleNamespace(status=404)
        files.list.return_value.execute.return_value = {
            'files': [{'id': 'missing', 'name': data['filename'] + '.manifest.json'}],
        }
        files.get_media.side_effect = missing
        self.assertEqual(provider.list(), [])

    def test_recovery_and_token_paths_fail_closed_on_races_and_symlinks(self):
        snapshot, data, item = self.local_snapshot()
        remote = Mock()
        remote.list.return_value = [item]
        remote.download.side_effect = lambda remote_item, target: target.write_bytes(snapshot.read_bytes())

        with patch.object(backups, 'provider', return_value=remote), patch.object(
                Path, 'is_symlink', side_effect=[False, True]):
            with self.assertRaisesRegex(backups.BackupError, 'unsafe'):
                backups.fetch(item.filename)
        (db.DATA / 'recovery').rmdir()

        recovery_target = db.DATA / 'recovery-target'
        recovery_target.mkdir()
        (db.DATA / 'recovery').symlink_to(recovery_target, target_is_directory=True)
        with patch.object(backups, 'provider', return_value=remote):
            with self.assertRaisesRegex(backups.BackupError, 'unsafe'):
                backups.fetch(item.filename)
        (db.DATA / 'recovery').unlink()

        with patch.object(backups, 'provider', return_value=remote), patch(
                'backend.backups.os.link', side_effect=FileExistsError):
            with self.assertRaisesRegex(backups.BackupError, 'already exists'):
                backups.fetch(item.filename)

        with patch.object(Path, 'is_symlink', side_effect=[False, True]):
            with self.assertRaisesRegex(backups.BackupError, 'unsafe'):
                backups._write_token(json.dumps({'refresh_token': 'private'}))
        (db.DATA / 'secrets').rmdir()

        secrets_target = db.DATA / 'secrets-target'
        secrets_target.mkdir()
        (db.DATA / 'secrets').symlink_to(secrets_target, target_is_directory=True)
        self.assertFalse(backups.readiness({'provider': 'google_drive'})['google_linked'])
        with self.assertRaisesRegex(backups.BackupError, 'unsafe'):
            backups._write_token(json.dumps({'refresh_token': 'private'}))

        backups._run_lock.acquire()
        try:
            with self.assertRaises(backups.BackupBusy):
                backups.google_unlink()
        finally:
            backups._run_lock.release()
