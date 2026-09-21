import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import backups,db,jobs
from backend.app import app,SCHEDULES


class BackupApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.data=patch.object(db,'DATA',Path(self.temp.name)); self.data.start()
        self.env=patch.dict('os.environ',{'DISABLE_JOBS':'1'}); self.env.start()
        db.initialize(); self.client=TestClient(app); self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None); self.env.stop(); self.data.stop(); self.temp.cleanup()

    def test_provider_payload_validation_and_redacted_get(self):
        self.assertEqual(self.client.put('/api/backups',json={'provider':'s3','bucket':'private','prefix':'p','region':'r','endpoint':'http://unsafe','encryption':'default'}).status_code,400)
        private={'provider':'s3','bucket':'private-bucket','prefix':'private-prefix','region':'private-region','endpoint':'https://private.example','encryption':'sse-kms'}
        response=self.client.put('/api/backups',json=private); self.assertEqual(response.status_code,200)
        body=response.text
        for value in ('private-bucket','private-prefix','private-region','private.example'):
            self.assertNotIn(value,body)
        self.assertTrue(response.json()['bucket_configured']); self.assertEqual(response.json()['encryption'],'sse-kms')

    def test_probe_reports_sanitized_failure_and_success(self):
        backups.set_config({'provider':'filesystem'})
        with patch.object(backups,'probe',side_effect=RuntimeError('PRIVATE URL')):
            response=self.client.post('/api/backups/test'); self.assertEqual(response.status_code,502); self.assertNotIn('PRIVATE',response.text)
        with patch.object(backups,'probe'):
            self.assertEqual(self.client.post('/api/backups/test').json(),{'ok':True})

    def test_manual_run_conflict_and_acceptance(self):
        with patch.object(backups,'start_async',return_value=False):
            self.assertEqual(self.client.post('/api/backups/run').status_code,409)
        with patch.object(backups,'start_async',return_value=True):
            response=self.client.post('/api/backups/run'); self.assertEqual(response.status_code,202); self.assertTrue(response.json()['accepted'])

    def test_google_unlink_removes_only_token(self):
        backups._write_token('{"refresh_token":"PRIVATE"}'); db.set_settings({'marker':'keep'})
        self.assertEqual(self.client.post('/api/backups/google/unlink').status_code,200)
        self.assertFalse(backups.token_file().exists()); self.assertEqual(db.settings()['marker'],'keep')


class BackupJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.data=patch.object(db,'DATA',Path(self.temp.name)); self.data.start(); db.initialize()
    def tearDown(self): self.data.stop(); self.temp.cleanup()

    def test_local_maintenance_success_is_independent_of_remote_failure(self):
        with patch.object(db,'backup',return_value=Path(self.temp.name)/'local.sqlite') as local,patch.object(backups,'run',side_effect=RuntimeError('remote')) as remote:
            jobs.maintenance()
        local.assert_called_once(); remote.assert_not_called()
        with db.connect() as con: self.assertIsNotNone(con.execute("SELECT last_success FROM job_status WHERE name='backup'").fetchone()[0])

    def test_offsite_job_delegates_to_serialized_worker(self):
        async def exercise():
            with patch.object(backups,'run',return_value=True) as run:
                await jobs.offsite_backup(); run.assert_called_once_with(wait=True)
        asyncio.run(exercise())

    def test_offsite_job_preserves_service_error_instead_of_overwriting_it(self):
        db.status('offsite_backup','The /offsite mount is missing')
        async def exercise():
            with patch.object(backups,'run',side_effect=backups.BackupError('The /offsite mount is missing')):
                await jobs.offsite_backup()
        asyncio.run(exercise())
        with db.connect() as con: error=con.execute("SELECT error FROM job_status WHERE name='offsite_backup'").fetchone()[0]
        self.assertEqual(error,'The /offsite mount is missing')

    def test_schedule_has_hourly_remote_retry_separate_from_maintenance(self):
        schedule={name:(task,interval) for name,task,interval in SCHEDULES}
        self.assertEqual(schedule['offsite_backup'],(jobs.offsite_backup,3600))
        self.assertEqual(schedule['maintenance'],(jobs.maintenance,3600))
        self.assertIsNot(schedule['offsite_backup'][0],schedule['maintenance'][0])

    def test_local_snapshot_is_due_daily_not_hourly(self):
        with patch.object(db,'backup') as backup,patch('backend.jobs.time.time',return_value=200000):
            jobs.maintenance(); backup.assert_called_once()
            jobs.maintenance(); backup.assert_called_once()

    def test_disabled_weather_and_unconfigured_sensor_record_actionable_status(self):
        async def exercise():
            await jobs.weather(); await jobs.collect()
        asyncio.run(exercise())
        with db.connect() as con:
            rows={r['name']:r['error'] for r in con.execute("SELECT name,error FROM job_status WHERE name IN ('weather','sensor')")}
        self.assertEqual(rows['weather'],'Forecasts are disabled in container settings')
        self.assertEqual(rows['sensor'],'Sensor is not configured')


if __name__=='__main__': unittest.main()
