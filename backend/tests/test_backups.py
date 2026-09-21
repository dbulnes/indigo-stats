import json
import os
import sys
import tempfile
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

    def tearDown(self):
        self.data_patch.stop(); self.offsite_tmp.cleanup(); self.data_tmp.cleanup()

    def mounted(self, path):
        return Path(path)==self.root

    def test_config_validation_and_public_status_are_redacted(self):
        with self.assertRaises(ValueError): backups.validate_config({'provider':'s3','bucket':'x','endpoint':'http://private.example'})
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

    def test_s3_upload_is_manifest_last_with_https_endpoint_and_sse(self):
        path=db.backup(); data=backups.manifest(path); events=[]
        client=Mock()
        client.upload_file.side_effect=lambda *a,**k:events.append(('database',a,k))
        client.head_object.return_value={'ContentLength':data['size'],'Metadata':{'sha256':data['sha256']}}
        client.put_object.side_effect=lambda **k:events.append(('manifest',(),k))
        module=types.SimpleNamespace(client=Mock(return_value=client))
        cfg={'provider':'s3','bucket':'private','prefix':'indigo','region':'us-west-2','endpoint':'https://objects.example','encryption':'sse-s3'}
        with patch.dict(sys.modules,{'boto3':module}),patch.dict(os.environ,{'BACKUP_S3_ACCESS_KEY_ID':'key','BACKUP_S3_SECRET_ACCESS_KEY':'secret'},clear=False):
            provider=backups.S3Provider(cfg); provider.upload(path,data)
        self.assertEqual([e[0] for e in events],['database','manifest'])
        self.assertEqual(module.client.call_args.kwargs['endpoint_url'],'https://objects.example')
        self.assertEqual(events[0][2]['ExtraArgs']['ServerSideEncryption'],'AES256')
        self.assertEqual(events[0][2]['ExtraArgs']['Metadata']['sha256'],data['sha256'])

    def test_s3_listing_ignores_incomplete_objects_and_deletes_only_pair(self):
        name='20260921T120000Z.sqlite'; payload=json.dumps({'format':1,'filename':name,'size':9,'created_at':1,'schema_version':6,'application_version':'x','sha256':'b'*64}).encode()
        client=Mock(); paginator=Mock(); paginator.paginate.return_value=[{'Contents':[{'Key':'p/'+name+'.manifest.json'},{'Key':'p/unrelated.txt'}]}]
        client.get_paginator.return_value=paginator; client.get_object.return_value={'Body':types.SimpleNamespace(read=lambda:payload)}
        client.head_object.return_value={'ContentLength':9,'Metadata':{'sha256':'b'*64}}
        module=types.SimpleNamespace(client=Mock(return_value=client)); cfg={'bucket':'bucket','prefix':'p','encryption':'default'}
        with patch.dict(sys.modules,{'boto3':module}),patch.dict(os.environ,{'BACKUP_S3_ACCESS_KEY_ID':'key','BACKUP_S3_SECRET_ACCESS_KEY':'secret'},clear=False): provider=backups.S3Provider(cfg)
        items=provider.list(); self.assertEqual([x.filename for x in items],[name])
        provider.delete(items[0]); deleted=client.delete_objects.call_args.kwargs['Delete']['Objects']
        self.assertEqual(deleted,[{'Key':'p/'+name},{'Key':'p/'+name+'.manifest.json'}])
        client.head_object.return_value={'ContentLength':8,'Metadata':{'sha256':'b'*64}}
        self.assertEqual(provider.list(),[])

    def test_drive_folder_is_rediscovered_and_upload_publishes_manifest_last(self):
        provider=object.__new__(backups.DriveProvider); service=Mock(); files=Mock(); service.files.return_value=files; provider.service=service
        files.list.return_value.execute.return_value={'files':[{'id':'existing'}]}
        self.assertEqual(provider._folder(),'existing'); files.create.assert_not_called()
        provider.folder='existing'; provider.probe(); service.about.return_value.get.return_value.execute.assert_called_once()
        files.list.return_value.execute.return_value={'files':[{'id':'owned','appProperties':{'indigoStatsType':'snapshot'}},{'id':'other','appProperties':{}}]}
        self.assertEqual([x['id'] for x in provider._find('sample.sqlite','snapshot')],['owned'])
        provider.folder='existing'; files.list.return_value.execute.return_value={'files':[]}
        path=db.backup(); data=backups.manifest(path)
        files.create.return_value.execute.side_effect=[{'id':'snapshot-id','size':str(data['size']),'appProperties':{'sha256':data['sha256']}},{'id':'manifest-id'}]
        provider.upload(path,data)
        names=[call.kwargs['body']['name'] for call in files.create.call_args_list]
        self.assertEqual(names,[path.name,path.name+'.manifest.json'])

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


if __name__=='__main__': unittest.main()
