import io
import hashlib
import json
import os
import sys
import tempfile
import time
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import backups, db


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.data_tmp=tempfile.TemporaryDirectory()
        self.offsite_tmp=tempfile.TemporaryDirectory()
        self.data_patch=patch.object(db,'DATA',Path(self.data_tmp.name)); self.data_patch.start()
        db.initialize()
        self.root=Path(self.offsite_tmp.name)
        backups._oauth_states.clear()

    def tearDown(self):
        backups._oauth_states.clear()
        self.data_patch.stop(); self.offsite_tmp.cleanup(); self.data_tmp.cleanup()

    def mounted(self, path):
        return Path(path)==self.root

    def test_config_validation_and_public_status_are_redacted(self):
        with self.assertRaises(ValueError): backups.validate_config({'provider':'s3','bucket':'x','endpoint':'http://private.example'})
        with self.assertRaises(ValueError): backups.validate_config({'provider':'s3','bucket':'x','endpoint':'https://private.example/?secret=1'})
        cfg=backups.set_config({'provider':'s3','bucket':'private-bucket','prefix':'private-prefix','region':'west','endpoint':'https://private.example','encryption':'sse-s3'})
        self.assertNotIn('private-bucket',json.dumps(backups.public_status()))
        self.assertNotIn('private.example',json.dumps(backups.public_status()))
        self.assertNotEqual(backups.fingerprint(cfg),backups.fingerprint({'provider':'filesystem'}))

    def test_filesystem_manifest_last_round_trip_and_unrelated_file(self):
        snapshot=db.backup(); provider=backups.FilesystemProvider(self.root)
        unrelated=self.root/'unrelated'; unrelated.write_text('keep')
        with patch('os.path.ismount',side_effect=self.mounted):
            data=backups.manifest(snapshot); provider.upload(snapshot,data)
            items=provider.list(); self.assertEqual(len(items),1)
            self.assertTrue((self.root/'indigo-stats'/(snapshot.name+'.manifest.json')).exists())
            target=db.DATA/'download.sqlite'; provider.download(items[0],target)
            self.assertEqual(backups.checksum(target),data['sha256'])
            provider.delete(items[0]); self.assertEqual(unrelated.read_text(),'keep')

    def test_filesystem_rejects_missing_mount_and_symlink(self):
        provider=backups.FilesystemProvider(self.root)
        with patch('os.path.ismount',return_value=False),self.assertRaises(backups.BackupError): provider.probe()
        link=self.root/'indigo-stats'; link.symlink_to(db.DATA)
        with patch('os.path.ismount',side_effect=self.mounted),self.assertRaises(backups.BackupError): provider.probe()

    def test_filesystem_rejects_data_subdirectory_and_cleans_partial_copy(self):
        nested=db.DATA/'offsite'; nested.mkdir(); provider=backups.FilesystemProvider(nested)
        with patch('os.path.ismount',return_value=True),self.assertRaises(backups.BackupError): provider.probe()
        snapshot=db.backup(); provider=backups.FilesystemProvider(self.root); data=backups.manifest(snapshot)
        with patch('os.path.ismount',side_effect=self.mounted),patch('shutil.copyfileobj',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError): provider.upload(snapshot,data)
        self.assertFalse((self.root/'indigo-stats'/snapshot.name).exists())
        self.assertEqual(list((self.root/'indigo-stats').glob('*.partial')),[])

    def test_reconcile_is_idempotent_and_prunes_only_completed_items(self):
        path=db.backup(); data=backups.manifest(path)
        remote=Mock(); remote.list.return_value=[]; remote.upload.return_value='opaque'
        remote.delete=Mock(); remote.probe=Mock()
        backups.set_config({'provider':'filesystem'})
        with patch.object(backups,'provider',return_value=remote):
            self.assertTrue(backups.run())
        remote.upload.assert_called_once()
        remote.reset_mock(); remote.list.return_value=[backups.RemoteSnapshot(path.name,data['sha256'],data['size'],data['created_at'],'opaque')]
        with patch.object(backups,'provider',return_value=remote): self.assertTrue(backups.run())
        remote.upload.assert_not_called(); remote.delete.assert_not_called()

    def test_concurrent_run_is_rejected(self):
        backups._run_lock.acquire()
        try: self.assertFalse(backups.run())
        finally: backups._run_lock.release()

    def test_failure_is_sanitized_and_attempt_is_recorded(self):
        db.backup(); backups.set_config({'provider':'filesystem'})
        remote=Mock(); remote.probe=Mock(); remote.list.return_value=[]
        remote.upload.side_effect=RuntimeError('https://private.example SECRET')
        with patch.object(backups,'provider',return_value=remote):
            with self.assertRaises(RuntimeError): backups.run()
            with self.assertRaises(RuntimeError): backups.run()
        with db.connect() as con:
            job=con.execute("SELECT error FROM job_status WHERE name='offsite_backup'").fetchone()[0]
            transfer=con.execute('SELECT attempts,error FROM backup_transfers').fetchone()
        self.assertEqual(transfer[0],2); self.assertNotIn('private.example',job+transfer[1]); self.assertNotIn('SECRET',job+transfer[1])

    def test_reconciliation_processes_newest_first_and_switching_destinations_isolated(self):
        source=db.backup(); older=source.with_name('20260920T000000Z.sqlite'); newer=source.with_name('20260921T000000Z.sqlite')
        source.replace(older); newer.write_bytes(older.read_bytes())
        remote=Mock(); remote.probe=Mock(); remote.list.return_value=[]; remote.upload.return_value='remote'
        backups.set_config({'provider':'filesystem'})
        with patch.object(backups,'provider',return_value=remote): backups.run()
        self.assertEqual([call.args[0].name for call in remote.upload.call_args_list],[newer.name,older.name])
        remote.reset_mock(); remote.probe=Mock(); remote.list.return_value=[]; remote.upload.return_value='remote'
        cfg={'provider':'s3','bucket':'other','prefix':'p','region':'','endpoint':'','encryption':'default'}; backups.set_config(cfg)
        with patch.object(backups,'provider',return_value=remote): backups.run()
        with db.connect() as con: destinations=con.execute('SELECT COUNT(DISTINCT destination) FROM backup_transfers').fetchone()[0]
        self.assertEqual(destinations,2)

    def test_file_secret_loading_and_missing_file_are_sanitized(self):
        secret=Path(self.data_tmp.name)/'secret'; secret.write_text('value\n')
        with patch.dict(os.environ,{'EXAMPLE_FILE':str(secret)},clear=False): self.assertEqual(backups._secret('EXAMPLE'),'value')
        with patch.dict(os.environ,{'EXAMPLE_FILE':'/private/missing/path'},clear=False):
            with self.assertRaises(backups.BackupError) as error: backups._secret('EXAMPLE')
        self.assertNotIn('/private/missing/path',str(error.exception))

    def test_remote_retention_deletes_only_oldest_completed_snapshot(self):
        local=db.backup(); data=backups.manifest(local)
        backups.set_config({'provider':'filesystem'}); remote=Mock(); remote.probe=Mock(); remote.upload.return_value='new'
        old=[backups.RemoteSnapshot(f'20260801T0000{i:02d}Z.sqlite','a'*64,1,i) for i in range(30)]
        remote.list.return_value=[backups.RemoteSnapshot(local.name,'b'*64,data['size'],99)]+old
        with patch.object(backups,'provider',return_value=remote): backups.run()
        remote.delete.assert_called_once_with(old[0])

    def test_fetch_refuses_overwrite_and_verifies(self):
        path=db.backup(); data=backups.manifest(path)
        item=backups.RemoteSnapshot(path.name,data['sha256'],data['size'],data['created_at'],'remote')
        remote=Mock(); remote.list.return_value=[item]
        remote.download.side_effect=lambda _item,target: target.write_bytes(path.read_bytes())
        with patch.object(backups,'provider',return_value=remote):
            recovered=backups.fetch(path.name,{'provider':'filesystem'})
            self.assertEqual(backups.checksum(recovered),data['sha256'])
            with self.assertRaises(backups.BackupError): backups.fetch(path.name,{'provider':'filesystem'})

    def test_fetch_removes_corrupt_partial_and_does_not_touch_live_database(self):
        path=db.backup(); data=backups.manifest(path); live_before=backups.checksum(db.DATA/'indigo.sqlite')
        item=backups.RemoteSnapshot(path.name,data['sha256'],data['size'],data['created_at'],'remote')
        remote=Mock(); remote.list.return_value=[item]; remote.download.side_effect=lambda _i,p:p.write_bytes(b'bad')
        with patch.object(backups,'provider',return_value=remote),self.assertRaises(backups.BackupError): backups.fetch(path.name,{'provider':'filesystem'})
        self.assertEqual(backups.checksum(db.DATA/'indigo.sqlite'),live_before)
        self.assertFalse((db.DATA/'recovery'/path.name).exists())

    def test_fetch_cleans_database_when_manifest_publication_fails(self):
        path=db.backup(); data=backups.manifest(path)
        item=backups.RemoteSnapshot(path.name,data['sha256'],data['size'],data['created_at'],'remote')
        remote=Mock(); remote.list.return_value=[item]
        remote.download.side_effect=lambda _item,target: target.write_bytes(path.read_bytes())
        real_link=os.link
        def publish(source,target,**kwargs):
            if str(target).endswith('.manifest.json'): raise OSError('disk failure')
            return real_link(source,target,**kwargs)
        with patch.object(backups,'provider',return_value=remote),patch('backend.backups.os.link',side_effect=publish):
            with self.assertRaises(OSError): backups.fetch(path.name,{'provider':'filesystem'})
        self.assertFalse((db.DATA/'recovery'/path.name).exists())
        self.assertFalse((db.DATA/'recovery'/(path.name+'.manifest.json')).exists())

    def test_s3_upload_is_manifest_last_with_https_endpoint_and_sse(self):
        path=db.backup(); data=backups.manifest(path); events=[]
        client=Mock()
        client.upload_file.side_effect=lambda *a,**k:events.append(('database',a,k))
        client.head_object.return_value={'ContentLength':data['size'],'Metadata':{'sha256':data['sha256']},'ServerSideEncryption':'AES256'}
        client.put_object.side_effect=lambda **k:events.append(('manifest',(),k))
        client.get_object.side_effect=[
            {'Body':io.BytesIO(path.read_bytes())},
            {'Body':io.BytesIO(backups._manifest_bytes(data))},
        ]
        module=types.SimpleNamespace(client=Mock(return_value=client))
        cfg={'provider':'s3','bucket':'private','prefix':'indigo','region':'us-west-2','endpoint':'https://objects.example','encryption':'sse-s3'}
        with patch.dict(sys.modules,{'boto3':module}),patch.dict(os.environ,{'BACKUP_S3_ACCESS_KEY_ID':'key','BACKUP_S3_SECRET_ACCESS_KEY':'secret'},clear=False):
            provider=backups.S3Provider(cfg); provider.upload(path,data)
        self.assertEqual([e[0] for e in events],['database','manifest'])
        self.assertEqual(module.client.call_args.kwargs['endpoint_url'],'https://objects.example')
        self.assertEqual(events[0][2]['ExtraArgs']['ServerSideEncryption'],'AES256')
        self.assertEqual(events[0][2]['ExtraArgs']['Metadata']['sha256'],data['sha256'])
        self.assertEqual(events[0][2]['ExtraArgs']['ChecksumAlgorithm'],'SHA256')

    def test_s3_rejects_corrupt_readback_before_manifest_publication(self):
        path = db.backup()
        data = backups.manifest(path)
        client = Mock()
        client.head_object.return_value = {
            'ContentLength': data['size'],
            'Metadata': {'sha256': data['sha256']},
        }
        client.get_object.return_value = {'Body': io.BytesIO(b'corrupt')}
        module = types.SimpleNamespace(client=Mock(return_value=client))
        cfg = {'bucket': 'bucket', 'prefix': 'p', 'encryption': 'default'}
        env = {'BACKUP_S3_ACCESS_KEY_ID': 'key', 'BACKUP_S3_SECRET_ACCESS_KEY': 'secret'}
        with patch.dict(sys.modules, {'boto3': module}), patch.dict(os.environ, env, clear=False):
            provider = backups.S3Provider(cfg)
            with self.assertRaises(backups.BackupError):
                provider.upload(path, data)
        client.put_object.assert_not_called()

    def test_s3_listing_ignores_incomplete_objects_and_deletes_only_pair(self):
        name='20260921T120000Z.sqlite'; payload=json.dumps({'format':1,'filename':name,'size':9,'created_at':1,'schema_version':6,'application_version':'x','sha256':'b'*64}).encode()
        client=Mock(); paginator=Mock(); paginator.paginate.return_value=[{'Contents':[{'Key':'p/'+name+'.manifest.json'},{'Key':'p/unrelated.txt'}]}]
        client.get_paginator.return_value=paginator; client.get_object.side_effect=lambda **_: {'Body':io.BytesIO(payload)}
        client.head_object.return_value={'ContentLength':9,'Metadata':{'sha256':'b'*64}}
        client.delete_objects.return_value={}
        module=types.SimpleNamespace(client=Mock(return_value=client)); cfg={'bucket':'bucket','prefix':'p','encryption':'default'}
        with patch.dict(sys.modules,{'boto3':module}),patch.dict(os.environ,{'BACKUP_S3_ACCESS_KEY_ID':'key','BACKUP_S3_SECRET_ACCESS_KEY':'secret'},clear=False): provider=backups.S3Provider(cfg)
        items=provider.list(); self.assertEqual([x.filename for x in items],[name])
        provider.delete(items[0]); deleted=client.delete_objects.call_args.kwargs['Delete']['Objects']
        self.assertEqual(deleted,[{'Key':'p/'+name},{'Key':'p/'+name+'.manifest.json'}])
        client.head_object.return_value={'ContentLength':8,'Metadata':{'sha256':'b'*64}}
        self.assertEqual(provider.list(),[])
        client.delete_objects.return_value={'Errors':[{'Code':'AccessDenied'}]}
        with self.assertRaises(backups.BackupError): provider.delete(items[0])

    def test_drive_folder_is_rediscovered_and_upload_publishes_manifest_last(self):
        provider=object.__new__(backups.DriveProvider); service=Mock(); files=Mock(); service.files.return_value=files; provider.service=service
        files.list.return_value.execute.return_value={'files':[{'id':'existing'}]}
        self.assertEqual(provider._folder(),'existing'); files.create.assert_not_called()
        provider.folder='existing'; provider.probe(); service.about.return_value.get.return_value.execute.assert_called_once()
        files.list.return_value.execute.return_value={'files':[{'id':'owned','appProperties':{'indigoStatsType':'snapshot'}},{'id':'other','appProperties':{}}]}
        self.assertEqual([x['id'] for x in provider._find('sample.sqlite','snapshot')],['owned'])
        provider.folder='existing'; files.list.return_value.execute.return_value={'files':[]}
        path=db.backup(); data=backups.manifest(path)
        manifest_payload=backups._manifest_bytes(data)
        files.create.return_value.execute.side_effect=[
            {'id':'snapshot-id','size':str(data['size']),'sha256Checksum':data['sha256']},
            {'id':'manifest-id','size':str(len(manifest_payload)),'sha256Checksum':hashlib.sha256(manifest_payload).hexdigest()},
        ]
        provider.upload(path,data)
        names=[call.kwargs['body']['name'] for call in files.create.call_args_list]
        self.assertEqual(names,[path.name,path.name+'.manifest.json'])

    def test_drive_failed_replacement_preserves_previous_pair(self):
        provider=object.__new__(backups.DriveProvider); provider.folder='existing'
        service=Mock(); files=Mock(); service.files.return_value=files; provider.service=service
        files.list.return_value.execute.side_effect=[
            {'files':[{'id':'old-snapshot','appProperties':{'indigoStatsType':'snapshot'}}]},
            {'files':[{'id':'old-manifest','appProperties':{'indigoStatsType':'manifest'}}]},
        ]
        files.create.return_value.execute.side_effect=RuntimeError('temporary upload failure')
        path=db.backup(); data=backups.manifest(path)
        with self.assertRaises(RuntimeError): provider.upload(path,data)
        files.delete.assert_not_called()

    def test_failed_transfer_clears_previous_completion(self):
        destination='d'; name='20260921T120000Z.sqlite'
        original={'filename':name,'sha256':'a'*64,'size':1}
        replacement={'filename':name,'sha256':'b'*64,'size':2}
        backups._record_attempt(destination,original,reference='old',complete=True)
        backups._record_attempt(destination,replacement,error='failed')
        with db.connect() as con:
            row=con.execute('SELECT checksum,remote_reference,completed_at,error FROM backup_transfers').fetchone()
        self.assertEqual(row['checksum'],'b'*64)
        self.assertIsNone(row['remote_reference']); self.assertIsNone(row['completed_at'])
        self.assertEqual(row['error'],'failed')
        backups._record_attempt(destination,replacement,reference='new',complete=True)
        backups._record_attempt(destination,replacement,error='failed again')
        with db.connect() as con:
            row=con.execute('SELECT remote_reference,completed_at,error FROM backup_transfers').fetchone()
        self.assertIsNone(row['remote_reference']); self.assertIsNone(row['completed_at'])
        self.assertEqual(row['error'],'failed again')

    def test_retention_retries_without_a_new_upload(self):
        path=db.backup(); data=backups.manifest(path)
        current=backups.RemoteSnapshot(path.name,data['sha256'],data['size'],data['created_at'])
        old=[backups.RemoteSnapshot(f'20260801T0000{i:02d}Z.sqlite','a'*64,1,i) for i in range(30)]
        remote=Mock(); remote.list.return_value=[current]+old
        backups.set_config({'provider':'filesystem'})
        with patch.object(backups,'provider',return_value=remote): backups.run()
        remote.upload.assert_not_called(); remote.delete.assert_called_once_with(old[0])

    def test_drive_invalid_token_requires_relink_without_exposing_token(self):
        backups._write_token('{"refresh_token":"PRIVATE INVALID"}')
        with self.assertRaises(backups.RelinkRequired) as error: backups.DriveProvider()
        self.assertNotIn('PRIVATE',str(error.exception)); self.assertNotIn('INVALID',str(error.exception))

    def test_google_oauth_uses_drive_file_pkce_and_secure_token_file(self):
        captured={}
        class Flow:
            credentials=types.SimpleNamespace(to_json=lambda:'{"refresh_token":"PRIVATE"}')
            @classmethod
            def from_client_config(cls,config,scopes,redirect_uri):
                captured.update(config=config,scopes=scopes,redirect_uri=redirect_uri); return cls()
            def authorization_url(self,**kwargs): captured.update(kwargs); return ('https://accounts.google.test/auth','state')
            def fetch_token(self,**kwargs): captured['fetch']=kwargs
        package=types.ModuleType('google_auth_oauthlib'); flow_module=types.ModuleType('google_auth_oauthlib.flow'); flow_module.Flow=Flow
        env={'BACKUP_GOOGLE_CLIENT_ID':'id','BACKUP_GOOGLE_CLIENT_SECRET':'secret','BACKUP_GOOGLE_CALLBACK_URI':'https://stats.example/api/backups/google/callback'}
        with patch.dict(sys.modules,{'google_auth_oauthlib':package,'google_auth_oauthlib.flow':flow_module}),patch.dict(os.environ,env,clear=False):
            self.assertTrue(backups.google_authorization_url().startswith('https://accounts.google.test'))
            state=next(iter(backups._oauth_states)); backups.google_callback('code',state)
        self.assertEqual(captured['scopes'],[backups.SCOPE]); self.assertEqual(captured['access_type'],'offline')
        self.assertEqual(captured['code_challenge_method'],'S256'); self.assertNotIn('=',captured['code_challenge'])
        token=backups.token_file(); self.assertEqual(token.stat().st_mode & 0o777,0o600)
        self.assertNotIn('PRIVATE',json.dumps(db.settings()))

    def test_safe_error_and_auth_error_branches(self):
        self.assertEqual(backups._safe_error(backups.RelinkRequired('relink')), 'Google Drive must be linked again')
        refresh_err = type('RefreshError', (Exception,), {})()
        self.assertEqual(backups._safe_error(refresh_err), 'Google Drive must be linked again')
        http_401 = Exception()
        http_401.resp = types.SimpleNamespace(status=401)
        self.assertEqual(backups._safe_error(http_401), 'Google Drive must be linked again')
        self.assertEqual(backups._safe_error(backups.BackupError('custom error')), 'custom error')
        self.assertEqual(backups._safe_error(RuntimeError('generic')), 'RuntimeError: transfer failed; will retry')

    def test_config_validation_edge_cases(self):
        with self.assertRaises(ValueError): backups.validate_config("not-a-dict")
        with self.assertRaises(ValueError): backups.validate_config({'provider': 'invalid'})
        with self.assertRaises(ValueError): backups.validate_config({'provider': 'disabled', 'extra': 1})
        self.assertEqual(backups.validate_config({'provider': 'disabled'}), {'provider': 'disabled'})
        self.assertEqual(backups.validate_config({'provider': 'google_drive'}), {'provider': 'google_drive'})
        with self.assertRaises(ValueError): backups.validate_config({'provider': 's3', 'bucket': ''})
        with self.assertRaises(ValueError): backups.validate_config({'provider': 's3', 'bucket': 'a' * 256})
        with self.assertRaises(ValueError): backups.validate_config({'provider': 's3', 'bucket': 'b', 'prefix': 'a/../b'})
        with self.assertRaises(ValueError): backups.validate_config({'provider': 's3', 'bucket': 'b', 'prefix': 'a' * 513})
        with self.assertRaises(ValueError): backups.validate_config({'provider': 's3', 'bucket': 'b', 'encryption': 'invalid'})

    def test_parse_manifest_validation(self):
        with self.assertRaises(backups.BackupError):
            backups._parse_manifest(b'{"format": 2, "filename": "20260921T120000Z.sqlite", "sha256": "' + b'0'*64 + b'", "size": 10, "created_at": 1}')
        with self.assertRaises(backups.BackupError):
            backups._parse_manifest(b'{"format": 1, "filename": "bad-name.txt", "sha256": "' + b'0'*64 + b'", "size": 10, "created_at": 1}')
        with self.assertRaises(backups.BackupError):
            backups._parse_manifest(b'{"format": 1, "filename": "20260921T120000Z.sqlite", "sha256": "badhex", "size": 10, "created_at": 1}')
        with self.assertRaises(backups.BackupError):
            backups._parse_manifest(b'{"format": 1, "filename": "20260921T120000Z.sqlite", "sha256": "' + b'0'*64 + b'", "size": 0, "created_at": 1}')
        with self.assertRaises(backups.BackupError):
            backups._parse_manifest(b'invalid json')

    def test_filesystem_provider_edge_cases(self):
        provider = backups.FilesystemProvider(self.root)
        with patch('os.path.ismount', side_effect=self.mounted), patch('os.access', return_value=False):
            with self.assertRaises(backups.BackupError) as err:
                provider.probe()
            self.assertIn('not writable', str(err.exception))

        with patch('os.path.ismount', side_effect=self.mounted), patch('os.access', side_effect=[True, False]):
            (self.root / 'indigo-stats').mkdir(exist_ok=True)
            with self.assertRaises(backups.BackupError) as err:
                provider.probe()
            self.assertIn('not writable', str(err.exception))

        with self.assertRaises(backups.BackupError) as err:
            provider._validate_name('../sneaky')
        self.assertIn('Invalid remote backup name', str(err.exception))

        import shutil
        shutil.rmtree(self.root / 'indigo-stats', ignore_errors=True)
        (self.root / 'indigo-stats').symlink_to(self.root)
        with patch('os.path.ismount', side_effect=self.mounted):
            with self.assertRaises(backups.BackupError) as err:
                provider._open_root(create=False).__enter__()
        self.assertIn('unsafe', str(err.exception))
        (self.root / 'indigo-stats').unlink()

        snapshot = db.backup()
        data = backups.manifest(snapshot)
        with patch('os.path.ismount', side_effect=self.mounted), patch.object(
                provider, '_file_checksum', return_value=('wrong-checksum', data['size'])):
            with self.assertRaises(backups.BackupError) as err:
                provider.upload(snapshot, data)
            self.assertIn('Off-server copy verification failed', str(err.exception))

        with patch('os.path.ismount', side_effect=self.mounted):
            (self.root / 'indigo-stats').mkdir(exist_ok=True)
            corrupt = self.root / 'indigo-stats' / 'corrupt.sqlite.manifest.json'
            corrupt.write_text('bad json')
            items = provider.list()
            self.assertEqual(items, [])

    def test_s3_provider_edge_cases(self):
        with patch.dict(sys.modules, {'boto3': None}):
            with self.assertRaises(backups.BackupError) as err:
                backups.S3Provider({'bucket': 'b', 'encryption': 'default'})
            self.assertIn('S3 support is not installed', str(err.exception))

        env = {
            'BACKUP_S3_ACCESS_KEY_ID': 'key',
            'BACKUP_S3_SECRET_ACCESS_KEY': 'secret',
            'BACKUP_S3_KMS_KEY_ID': 'custom-kms-key',
        }
        with patch.dict(os.environ, env, clear=False), patch('boto3.client') as mock_boto:
            mock_s3 = Mock()
            mock_boto.return_value = mock_s3
            provider = backups.S3Provider({'bucket': 'test-bucket', 'prefix': 'p', 'region': 'us-east-1', 'encryption': 'sse-kms'})
            snapshot = db.backup()
            data = backups.manifest(snapshot)
            payload = snapshot.read_bytes()
            manifest_payload = backups._manifest_bytes(data)
            mock_s3.head_object.return_value = {
                'ContentLength': data['size'], 'Metadata': {'sha256': data['sha256']},
                'ServerSideEncryption': 'aws:kms',
            }
            mock_s3.get_object.side_effect = lambda **kwargs: {
                'Body': io.BytesIO(manifest_payload if kwargs['Key'].endswith('.json') else payload),
            }
            provider.upload(snapshot, data)
            upload_extra = mock_s3.upload_file.call_args[1]['ExtraArgs']
            self.assertEqual(upload_extra['ServerSideEncryption'], 'aws:kms')
            self.assertEqual(upload_extra['SSEKMSKeyId'], 'custom-kms-key')

            mock_s3.head_object.return_value = {'ContentLength': 999999, 'Metadata': {'sha256': 'wrong'}}
            with self.assertRaises(backups.BackupError) as err:
                provider.upload(snapshot, data)
            self.assertIn('S3 upload verification failed', str(err.exception))

            mock_s3.get_paginator.return_value.paginate.return_value = [
                {'Contents': [{'Key': 'p/bad.sqlite.manifest.json'}]}
            ]
            mock_s3.get_object.side_effect = RuntimeError('s3 error')
            with self.assertRaises(RuntimeError):
                provider.list()

            target = db.DATA / 's3_download.sqlite'
            item = backups.RemoteSnapshot('test.sqlite', 'sha', 10, 1, 'p/test.sqlite')
            provider.download(item, target)
            mock_s3.download_file.assert_called_once_with('test-bucket', 'p/test.sqlite', str(target))

    def test_drive_provider_full(self):
        with patch.dict(sys.modules, {'google.oauth2.credentials': None}):
            with self.assertRaises(backups.BackupError) as err:
                backups.DriveProvider()
            self.assertIn('Google Drive support is not installed', str(err.exception))

        token_path = backups.token_file()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text('{"token": "old"}')

        mock_creds = Mock()
        mock_creds.expired = True
        mock_creds.refresh_token = 'refresh-token'
        mock_creds.valid = True
        mock_creds.to_json.return_value = '{"token": "refreshed"}'

        with patch('google.oauth2.credentials.Credentials.from_authorized_user_file', return_value=mock_creds), \
             patch('google.auth.transport.requests.Request'), \
             patch('googleapiclient.discovery.build') as mock_build:
            mock_service = Mock()
            mock_build.return_value = mock_service
            mock_service.files.return_value.list.return_value.execute.return_value = {'files': []}
            mock_service.files.return_value.create.return_value.execute.return_value = {'id': 'new-folder-id'}
            dp = backups.DriveProvider()
            mock_creds.refresh.assert_called_once()
            self.assertIsNone(dp.folder)

            # Upload auto-creates folder if None
            path = db.backup()
            data = backups.manifest(path)
            mock_service.files.return_value.create.return_value.execute.side_effect = [
                {'id': 'new-folder-id'},
                {'id': 'snap-id', 'size': '999', 'appProperties': {'sha256': 'wrong'}},
            ]
            with self.assertRaises(backups.BackupError) as err:
                dp.upload(path, data)
            self.assertIn('Google Drive upload verification failed', str(err.exception))

            dp.folder = None
            self.assertEqual(dp.list(), [])

            dp.folder = 'folder-id'
            auth_err = Exception()
            auth_err.resp = types.SimpleNamespace(status=401)
            mock_service.files.return_value.list.return_value.execute.side_effect = None
            mock_service.files.return_value.list.return_value.execute.return_value = {
                'files': [{'id': 'm1', 'name': '20260921T120000Z.sqlite.manifest.json'}],
            }
            mock_service.files.return_value.get_media.side_effect = auth_err
            with self.assertRaises(backups.RelinkRequired):
                dp.list()

            mock_service.files.return_value.get_media.side_effect = RuntimeError('download error')
            with self.assertRaises(RuntimeError):
                dp.list()

            # list() successful manifest parsing and snapshot match
            valid_manifest = json.dumps({
                'format': 1, 'filename': '20260921T120000Z.sqlite', 'sha256': 'a' * 64,
                'size': 1234, 'created_at': 1000, 'schema_version': 1, 'application_version': '0.1.0'
            }).encode()
            mock_service.files.return_value.get_media.side_effect = None
            with patch('googleapiclient.http.MediaIoBaseDownload') as mock_downloader:
                def fake_init(fd, request):
                    fd.write(valid_manifest)
                    m = Mock()
                    m.next_chunk.return_value = (None, True)
                    return m
                mock_downloader.side_effect = fake_init
                dp._find = Mock(return_value=[{
                    'id': 'snap-file-id',
                    'sha256Checksum': 'a' * 64,
                    'size': '1234',
                }])
                listed = dp.list()
                self.assertEqual(len(listed), 1)
                self.assertEqual(listed[0].filename, '20260921T120000Z.sqlite')
                self.assertEqual(listed[0].reference, 'snap-file-id')

            mock_service.files.return_value.get_media.side_effect = None
            item = backups.RemoteSnapshot('snap.sqlite', 'sha', 10, 1, 'ref-id')
            target = db.DATA / 'drive_dl.sqlite'
            with patch('googleapiclient.http.MediaIoBaseDownload') as mock_downloader:
                mock_dl_inst = Mock()
                mock_dl_inst.next_chunk.return_value = (None, True)
                mock_downloader.return_value = mock_dl_inst
                dp.download(item, target)
                self.assertTrue(target.exists())

            dp._find = Mock(return_value=[{'id': 'f1'}])
            mock_service.files.return_value.delete.return_value.execute.reset_mock()
            dp.delete(item)
            self.assertEqual(mock_service.files.return_value.delete.return_value.execute.call_count, 2)

    def test_provider_factory_and_readiness_and_probe(self):
        with self.assertRaises(backups.BackupError) as err:
            backups.provider({'provider': 'disabled'})
        self.assertIn('Off-server backups are disabled', str(err.exception))

        prov = backups.provider({'provider': 'filesystem'})
        self.assertIsInstance(prov, backups.FilesystemProvider)

        auth_err = Exception()
        auth_err.resp = types.SimpleNamespace(status=401)
        with patch('backend.backups.DriveProvider', side_effect=auth_err):
            with self.assertRaises(backups.RelinkRequired):
                backups.provider({'provider': 'google_drive'})

        with patch('backend.backups.DriveProvider', side_effect=RuntimeError('generic drive error')):
            with self.assertRaises(RuntimeError):
                backups.provider({'provider': 'google_drive'})

        with patch('backend.backups._secret', side_effect=backups.BackupError('secret error')):
            r = backups.readiness({'provider': 's3'})
            self.assertFalse(r['credentials_configured'])

        auth_err = Exception()
        auth_err.resp = types.SimpleNamespace(status=401)
        mock_p = Mock()
        mock_p.probe.side_effect = auth_err
        with patch('backend.backups.provider', return_value=mock_p):
            with self.assertRaises(backups.RelinkRequired):
                backups.probe()

        mock_p = Mock()
        mock_p.probe.side_effect = RuntimeError('probe failure')
        with patch('backend.backups.provider', return_value=mock_p):
            with self.assertRaises(RuntimeError):
                backups.probe()

    def test_execute_disabled_and_start_async_and_remote_list(self):
        backups.set_config({'provider': 'disabled'})
        self.assertTrue(backups.run(wait=True))
        with db.connect() as con:
            row = con.execute("SELECT last_success FROM job_status WHERE name='offsite_backup'").fetchone()
            self.assertTrue(row is None or row[0] is None)
        self.assertIsNone(backups.public_status()['last_success'])

        with patch('backend.backups._execute') as mock_exec:
            self.assertTrue(backups.start_async())
            time.sleep(0.1)
            mock_exec.assert_called_once()

        with patch('backend.backups._execute', side_effect=RuntimeError('execute error')):
            self.assertTrue(backups.start_async())
            time.sleep(0.1)

        with patch('backend.backups.provider') as mock_prov:
            mock_p = Mock()
            mock_prov.return_value = mock_p
            mock_p.list.return_value = [backups.RemoteSnapshot('snap.sqlite', 'sha', 1, 1, 'ref')]
            res = backups.remote_list({'provider': 'filesystem'})
            self.assertEqual(len(res), 1)

    def test_fetch_integrity_failure(self):
        snapshot = db.backup()
        data = backups.manifest(snapshot)
        item = backups.RemoteSnapshot(snapshot.name, data['sha256'], data['size'], data['created_at'], 'ref')
        mock_p = Mock()
        mock_p.list.return_value = [item]

        def dummy_download(item, target):
            target.write_bytes(snapshot.read_bytes())
        mock_p.download.side_effect = dummy_download

        mock_con = Mock()
        mock_con.execute.return_value.fetchone.return_value = ('corrupt',)
        with patch('backend.backups.provider', return_value=mock_p), \
             patch('backend.backups.checksum', return_value=data['sha256']), \
             patch('sqlite3.connect', return_value=mock_con):
            with self.assertRaises(backups.BackupError) as err:
                backups.fetch(snapshot.name, {'provider': 'filesystem'})
            self.assertIn('integrity check failed', str(err.exception))

    def test_google_oauth_edge_cases(self):
        with patch.dict(sys.modules, {'google_auth_oauthlib.flow': None}):
            with self.assertRaises(backups.BackupError) as err:
                backups.google_authorization_url()
            self.assertIn('Google Drive support is not installed', str(err.exception))

        env = {
            'BACKUP_GOOGLE_CLIENT_ID': 'id',
            'BACKUP_GOOGLE_CLIENT_SECRET': 'secret',
            'BACKUP_GOOGLE_CALLBACK_URI': 'http://insecure.example/cb',
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(backups.BackupError) as err:
                backups.google_authorization_url()
            self.assertIn('callback must use HTTPS', str(err.exception))

        env['BACKUP_GOOGLE_CALLBACK_URI'] = 'https://secure.example/cb'
        mock_flow = Mock()
        mock_flow.authorization_url.return_value = ('https://accounts.google.com/test', 'state')
        with patch.dict(os.environ, env, clear=False), \
             patch('google_auth_oauthlib.flow.Flow.from_client_config', return_value=mock_flow):
            backups._oauth_states['expired-state'] = ('verifier', 0)
            backups._oauth_states['valid-state'] = ('verifier', time.time() + 1000)
            backups.google_authorization_url()
            self.assertNotIn('expired-state', backups._oauth_states)

        with self.assertRaises(backups.BackupError) as err:
            backups.google_callback('code', 'nonexistent-or-expired')
        self.assertIn('session expired', str(err.exception))

        backups._oauth_states['valid-state-2'] = ('verifier', time.time() + 1000)
        with patch.dict(sys.modules, {'google_auth_oauthlib.flow': None}):
            with self.assertRaises(backups.BackupError) as err:
                backups.google_callback('code', 'valid-state-2')
            self.assertIn('Google Drive support is not installed', str(err.exception))

    def test_google_oauth_rejects_lookalike_localhost_and_bounds_state(self):
        class Flow:
            @classmethod
            def from_client_config(cls, *args, **kwargs): return cls()
            def authorization_url(self, **kwargs): return ('https://accounts.google.test/auth','ignored')
        package=types.ModuleType('google_auth_oauthlib')
        flow_module=types.ModuleType('google_auth_oauthlib.flow'); flow_module.Flow=Flow
        base={'BACKUP_GOOGLE_CLIENT_ID':'id','BACKUP_GOOGLE_CLIENT_SECRET':'secret'}
        with patch.dict(sys.modules,{'google_auth_oauthlib':package,'google_auth_oauthlib.flow':flow_module}):
            with patch.dict(os.environ,base|{'BACKUP_GOOGLE_CALLBACK_URI':'http://localhost.evil/callback'},clear=False):
                with self.assertRaises(backups.BackupError): backups.google_authorization_url()
            with patch.dict(os.environ,base|{'BACKUP_GOOGLE_CALLBACK_URI':'http://localhost:8000/callback'},clear=False):
                for _ in range(backups.MAX_OAUTH_STATES+5): backups.google_authorization_url()
        self.assertEqual(len(backups._oauth_states),backups.MAX_OAUTH_STATES)

    def test_async_failure_releases_run_lock(self):
        finished=threading.Event()
        def fail():
            try: raise RuntimeError('failure')
            finally: finished.set()
        with patch.object(backups,'_execute',side_effect=fail):
            self.assertTrue(backups.start_async()); self.assertTrue(finished.wait(2))
        self.assertTrue(backups._run_lock.acquire(timeout=2))
        backups._run_lock.release()


if __name__=='__main__': unittest.main()
