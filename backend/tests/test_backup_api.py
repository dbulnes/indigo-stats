import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from backend import backups,db,jobs
from backend.app import app,SCHEDULES


class BackupApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.data=patch.object(db,'DATA',Path(self.temp.name)); self.data.start()
        self.env=patch.dict('os.environ',{'DISABLE_JOBS':'1'}); self.env.start()
        db.initialize(); self.client=TestClient(app); self.client.__enter__()
        self.client.headers.update({'X-Indigo-Request': '1'})

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

    def test_google_connect(self):
        with patch.object(backups, 'google_authorization_url', return_value='https://accounts.google.com/o/oauth2/auth?client_id=test'):
            res = self.client.get('/api/backups/google/connect', follow_redirects=False)
            self.assertEqual(res.status_code, 302)
            self.assertEqual(res.headers['location'], 'https://accounts.google.com/o/oauth2/auth?client_id=test')

        with patch.object(backups, 'google_authorization_url', side_effect=backups.BackupError('OAuth not configured')):
            res = self.client.get('/api/backups/google/connect', follow_redirects=False)
            self.assertEqual(res.status_code, 400)
            self.assertIn('OAuth not configured', res.text)

    def test_google_callback(self):
        with patch.object(backups, 'google_callback') as mock_cb:
            res = self.client.get('/api/backups/google/callback?code=authcode&state=authstate')
            self.assertEqual(res.status_code, 200)
            self.assertIn('Google Drive linked', res.text)
            self.assertIn('Close Window', res.text)
            self.assertIn('callback-complete.js', res.text)
            self.assertNotIn('<script>', res.text)
            mock_cb.assert_called_once_with('authcode', 'authstate')

            script = self.client.get('/api/backups/google/callback-complete.js')
            self.assertEqual(script.status_code, 200)
            self.assertIn('window.location.origin', script.text)

        with patch.object(backups, 'google_callback', side_effect=backups.BackupError('expired session')):
            res = self.client.get('/api/backups/google/callback?code=authcode&state=authstate')
            self.assertEqual(res.status_code, 400)
            self.assertIn('expired session', res.text)

        with patch.object(backups, 'google_callback', side_effect=RuntimeError('oauth failed')):
            res = self.client.get('/api/backups/google/callback?code=authcode&state=authstate')
            self.assertEqual(res.status_code, 502)
            self.assertIn('Google authorization failed', res.text)

        res = self.client.get('/api/backups/google/callback?code=authcode&state=' + 'x' * 257)
        self.assertEqual(res.status_code, 422)

    def test_lifespan_starts_and_cancels_jobs(self):
        async def dummy_loop(name, task, interval):
            try:
                while True:
                    await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass

        with patch.dict('os.environ', {'DISABLE_JOBS': '0'}):
            with patch('backend.jobs.loop', side_effect=dummy_loop):
                with TestClient(app) as test_client:
                    res = test_client.get('/api/health')
                    self.assertEqual(res.status_code, 200)

    def test_mutations_require_ui_header_and_strict_payload_types(self):
        self.assertEqual(
            self.client.post('/api/backups/run', headers={'X-Indigo-Request': '0'}).status_code,
            403,
        )
        for provider in ([], {}, 1, None):
            response = self.client.put('/api/backups', json={'provider': provider})
            self.assertEqual(response.status_code, 400)
        response = self.client.put('/api/backups', json={
            'provider': 's3', 'bucket': ['not-a-string'], 'prefix': '', 'region': '',
            'endpoint': '', 'encryption': 'default',
        })
        self.assertEqual(response.status_code, 400)

    def test_cross_site_oauth_start_and_configuration_during_run_are_rejected(self):
        response=self.client.get('/api/backups/google/connect',headers={'Sec-Fetch-Site':'cross-site'})
        self.assertEqual(response.status_code,403)
        response=self.client.get('/api/backups/google/connect',headers={
            'Sec-Fetch-Site':'same-origin', 'Origin':'https://untrusted.example',
        })
        self.assertEqual(response.status_code,403)
        backups._run_lock.acquire()
        try:
            response=self.client.put('/api/backups',json={'provider':'disabled'})
            unlink=self.client.post('/api/backups/google/unlink')
        finally:
            backups._run_lock.release()
        self.assertEqual(response.status_code,409)
        self.assertEqual(unlink.status_code,409)


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

    def test_collect_success_stores_reading_raw_payload_and_status(self):
        db.set_settings({'sensor_host':'192.0.2.10'})
        response=Mock(); response.json.return_value={'SensorId':'synthetic','pm2_5_cf_1':12}
        client=AsyncMock(); client.__aenter__.return_value=client; client.get.return_value=response
        with patch.object(jobs.httpx,'AsyncClient',return_value=client),patch.object(jobs.time,'time',return_value=120):
            asyncio.run(jobs.collect())
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT pm25 FROM readings').fetchone()[0],12)
            self.assertIn('synthetic',con.execute('SELECT payload FROM raw_samples').fetchone()[0])
            status=con.execute("SELECT last_success,error FROM job_status WHERE name='sensor'").fetchone()
        self.assertIsNotNone(status['last_success']); self.assertIsNone(status['error'])

    def test_weather_success_stores_hourly_air_and_daily_rows(self):
        db.set_settings({'forecast_enabled':True,'latitude':1.0,'longitude':2.0})
        weather=Mock(); weather.json.return_value={
            'hourly':{
                'time':[100], 'temperature_2m':[70], 'relative_humidity_2m':[40],
                'uv_index':[3], 'precipitation_probability':[20], 'weather_code':[2],
                'wind_speed_10m':[8], 'apparent_temperature':[69], 'cloud_cover':[30],
            },
            'daily':{
                'time':[0], 'sunrise':[10], 'sunset':[20], 'weather_code':[2],
                'temperature_2m_max':[75], 'temperature_2m_min':[55],
            },
        }
        air=Mock(); air.json.return_value={'hourly':{'time':[100],'pm2_5':[5],'us_aqi':[21]}}
        client=AsyncMock(); client.__aenter__.return_value=client; client.get.side_effect=[weather,air]
        with patch.object(jobs.httpx,'AsyncClient',return_value=client),patch.object(jobs.time,'time',return_value=50):
            asyncio.run(jobs.weather())
        with db.connect() as con:
            kinds={row['kind']:row for row in con.execute('SELECT * FROM forecasts')}
            status=con.execute("SELECT last_success FROM job_status WHERE name='weather'").fetchone()
        self.assertEqual(kinds['weather']['weather_code'],2)
        self.assertEqual(kinds['air']['aqi'],21)
        self.assertEqual(kinds['daily']['temp_min'],55)
        self.assertIsNotNone(status['last_success'])


if __name__=='__main__': unittest.main()
