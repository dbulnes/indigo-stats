import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import db,jobs
from backend.air import aqi,normalize,nowcast,environment_values

class AirTests(unittest.TestCase):
    def test_epa_2024_breakpoints_and_truncation(self):
        for pm,expected in [(0,0),(9,50),(9.099,50),(9.1,51),(35.4,100),(35.5,101),(55.4,150),(55.5,151),(125.4,200),(125.5,201),(225.4,300),(225.5,301),(325.4,500),(400,500)]:
            self.assertEqual(aqi(pm),expected)
        self.assertIsNone(aqi(None)); self.assertIsNone(aqi(float('nan')))
    def test_channels_missing_values_and_correction(self):
        d={'SensorId':'test','pm2_5_cf_1':10,'pm2_5_cf_1_b':20,'current_humidity':50,'current_temp_f':80}
        r=normalize(d,{'pm_method':'epa2021'},123)
        self.assertAlmostEqual(r['pm25'],9.3)
        self.assertEqual(r['ts'],120)
        self.assertEqual(r['quality'],'channel_disagreement')
        d['pm2_5_cf_1_b']=-1
        self.assertEqual(normalize(d,{},123)['pm25'],10)
        d['pm2_5_cf_1']=None
        self.assertIsNone(normalize(d,{},123)['pm25'])
    def test_purpleair_temperature_and_raw_humidity_for_pm(self):
        temp,rh=environment_values(81,42)
        self.assertAlmostEqual(temp,73.4637)
        self.assertAlmostEqual(rh,67.9136)
        self.assertEqual(environment_values(81,42,'raw'),(81,42))
        self.assertEqual(environment_values(81,42,'simple'),(73,46))
        self.assertEqual(environment_values(None,None),(None,None))
        self.assertEqual(environment_values(81,100)[1],100)
        row=normalize({'SensorId':'test','current_temp_f':81,'current_humidity':42,'pm2_5_cf_1':15}, {'pm_method':'epa2021'},120)
        self.assertAlmostEqual(row['pm25'],9.9896)
        self.assertEqual(row['temperature_raw'],81)
    def test_nowcast_completeness_and_missing_hour_age(self):
        self.assertIsNone(nowcast([10,None,None,20]))
        self.assertEqual(nowcast([0,0]),0)
        self.assertAlmostEqual(nowcast([10,None,20]),12)
        self.assertEqual(nowcast([5]*12),5)

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(db,'DATA',Path(self.temp.name)); self.patch.start(); db.initialize()
    def tearDown(self):
        self.patch.stop(); self.temp.cleanup()
    def test_duplicate_safe_and_backup_restore(self):
        d={'SensorId':'test','pm2_5_cf_1':3}
        jobs.store_reading(d,{},120); jobs.store_reading(d,{},125)
        with db.connect() as con: self.assertEqual(con.execute('SELECT COUNT(*) FROM readings').fetchone()[0],1)
        path=db.backup()
        import sqlite3
        con=sqlite3.connect(path)
        self.assertEqual(con.execute('SELECT pm25 FROM readings').fetchone()[0],3); con.close()
    def test_local_backup_retention_permissions_and_job_status_history(self):
        folder=db.DATA/'backups'; folder.mkdir(exist_ok=True)
        for day in range(1,16): (folder/f'202601{day:02d}T000000Z.sqlite').write_bytes(b'old')
        with patch('backend.db.time.strftime',return_value='20260116T000000Z'):
            newest=db.backup()
        retained=sorted(folder.glob('*.sqlite'))
        self.assertEqual(len(retained),14); self.assertNotIn(folder/'20260101T000000Z.sqlite',retained)
        self.assertEqual(newest.stat().st_mode & 0o777,0o600)
        db.status('example',success=True); db.status('example','temporary failure')
        with db.connect() as con: row=con.execute("SELECT last_success,error FROM job_status WHERE name='example'").fetchone()
        self.assertIsNotNone(row['last_success']); self.assertEqual(row['error'],'temporary failure')
    def test_export_pages_every_row_and_escapes_formulas(self):
        from fastapi.testclient import TestClient
        from backend.app import app
        with db.connect() as con:
            con.executemany("INSERT INTO readings(ts,source_ts,pm25,method,quality) VALUES (?, '=untrusted', 2, 'cf1', '')",[(i*60,) for i in range(2050)])
        with patch.dict('os.environ',{'DISABLE_JOBS':'1'}),TestClient(app) as client:
            response=client.get('/api/export?start=0&end=123000')
            self.assertEqual(len(response.text.splitlines()),2051)
            self.assertIn("'=untrusted",response.text)
    def test_existing_raw_history_uses_consistent_estimated_view(self):
        from fastapi.testclient import TestClient
        from backend.app import app
        with db.connect() as con:
            con.execute("INSERT INTO readings(ts,temperature,temperature_raw,humidity,humidity_raw,method,quality) VALUES(60,81,81,42,42,'cf1','')")
        with patch.dict('os.environ',{'DISABLE_JOBS':'1'}),TestClient(app) as client:
            estimated=client.get('/api/history?start=0&end=120&environment=purpleair').json()['points'][0]
            raw=client.get('/api/history?start=0&end=120&environment=raw').json()['points'][0]
            self.assertAlmostEqual(estimated['temperature'],73.4637)
            self.assertEqual(raw['temperature'],81)
    def test_v1_migration_preserves_raw_readings_and_backup(self):
        import sqlite3
        with db.connect() as con:
            con.execute("INSERT INTO readings(ts,temperature_raw,humidity_raw,method,quality) VALUES(60,81,42,'cf1','')")
            con.execute('ALTER TABLE readings DROP COLUMN environment_mode')
            con.execute('ALTER TABLE forecasts DROP COLUMN uv_index')
            con.execute('ALTER TABLE forecasts DROP COLUMN precipitation_probability')
            con.execute('ALTER TABLE forecasts DROP COLUMN aqi')
            con.execute('ALTER TABLE forecasts DROP COLUMN weather_code')
            con.execute('ALTER TABLE forecasts DROP COLUMN wind_speed')
            con.execute('ALTER TABLE forecasts DROP COLUMN apparent_temperature')
            con.execute('ALTER TABLE forecasts DROP COLUMN cloud_cover')
            con.execute('ALTER TABLE forecasts DROP COLUMN sunrise')
            con.execute('ALTER TABLE forecasts DROP COLUMN sunset')
            con.execute('ALTER TABLE forecasts DROP COLUMN temp_min')
            con.execute('DROP TABLE backup_transfers')
            con.execute('PRAGMA user_version=1')
        db.initialize()
        with db.connect() as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],6)
            self.assertEqual(tuple(con.execute('SELECT temperature_raw,environment_mode FROM readings').fetchone()),(81,'raw'))
        backups=list((db.DATA/'backups').glob('*.sqlite'))
        self.assertEqual(len(backups),1)
        with sqlite3.connect(backups[0]) as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT temperature_raw FROM readings').fetchone()[0],81)
    def test_private_environment_validation_and_forecast_opt_in(self):
        from backend.config import import_environment
        with patch.dict('os.environ',{'FORECAST_ENABLED':'false'},clear=True):
            import_environment()
            self.assertIs(db.settings()['forecast_enabled'],False)
        for env in ({'FORECAST_ENABLED':'yes'}, {'FORECAST_LATITUDE':'private-invalid'}, {'SENSOR_HOST':'private-invalid'}):
            with patch.dict('os.environ',env,clear=True):
                with self.assertRaises(ValueError) as error: import_environment()
                self.assertNotIn('private-invalid',str(error.exception))
        with patch.dict('os.environ',{'FORECAST_LATITUDE':'10'},clear=True):
            with self.assertRaises(ValueError): import_environment()
    def test_version_guard(self):
        with db.connect() as con: con.execute('PRAGMA user_version=999')
        with self.assertRaises(RuntimeError): db.initialize()
    def test_forecasts_do_not_use_hindsight(self):
        from backend.app import forecast_rows
        jobs.store_forecasts([(100,200,'air',None,None,5,None,None,None),(199,200,'air',None,None,6,None,None,None),(201,200,'air',None,None,50,None,None,None)])
        with db.connect() as con:
            self.assertEqual(forecast_rows(con,200,201)[0]['pm25'],6)
            self.assertEqual(forecast_rows(con,200,201,False)[0]['pm25'],50)
    def test_api_never_exposes_private_config(self):
        from fastapi.testclient import TestClient
        from backend.app import app
        db.set_settings({'address':'PRIVATE ADDRESS','latitude':12.345,'longitude':67.89,'timezone':'America/Los_Angeles'})
        with patch.dict('os.environ',{'DISABLE_JOBS':'1'}),TestClient(app) as client:
            with patch.dict('os.environ',{'BACKUP_S3_ACCESS_KEY_ID':'PRIVATE KEY','BACKUP_S3_SECRET_ACCESS_KEY':'PRIVATE SECRET','DISABLE_JOBS':'1'}):
                for url in ['/api/status','/api/latest','/api/history?start=0&end=86400','/api/forecast','/api/settings','/api/backups']:
                    r=client.get(url); self.assertEqual(r.status_code,200)
                    self.assertNotIn('PRIVATE ADDRESS',r.text); self.assertNotIn('12.345',r.text)
                    self.assertNotIn('PRIVATE KEY',r.text); self.assertNotIn('PRIVATE SECRET',r.text)
            self.assertEqual(client.get('/api/history?start=10&end=5').status_code,400)
            self.assertEqual(client.get('/api/history?start=10&end=5').status_code,400)
    def test_units_and_daily_forecast_api(self):
        from fastapi.testclient import TestClient
        from backend.app import app
        with patch.dict('os.environ',{'DISABLE_JOBS':'1'}),TestClient(app) as client:
            r=client.get('/api/settings')
            self.assertEqual(r.json()['UNITS'],'imperial')
            res=client.post('/api/settings',json={'UNITS':'metric'})
            self.assertEqual(res.status_code,200)
            self.assertEqual(client.get('/api/settings').json()['UNITS'],'metric')
            bad=client.post('/api/settings',json={'UNITS':'kelvin'})
            self.assertEqual(bad.status_code,400)
            import time
            now=int(time.time())//3600*3600
            jobs.store_forecasts([
                (now, now+3600, 'weather', 72.0, 50.0, None, 5.0, 20.0, None, 2, 8.5, 74.0, 40.0, None, None, None),
                (now, now+3600, 'daily', 75.0, None, None, None, None, None, 2, None, None, None, now+20000, now+60000, 55.0)
            ])
            f_res=client.get('/api/forecast')
            self.assertEqual(f_res.status_code,200)
            f_data=f_res.json()
            self.assertIn('points',f_data)
            self.assertIn('daily',f_data)
            self.assertEqual(len(f_data['daily']),1)
            self.assertEqual(f_data['daily'][0]['temperature'],75.0)
            self.assertEqual(f_data['daily'][0]['temp_min'],55.0)
            self.assertEqual(f_data['daily'][0]['weather_code'],2)
            self.assertEqual(f_data['points'][0]['weather_code'],2)
            self.assertEqual(f_data['points'][0]['wind_speed'],8.5)
    def test_security_headers_and_empty_latest_state(self):
        from fastapi.testclient import TestClient
        from backend.app import app
        with patch.dict('os.environ',{'DISABLE_JOBS':'1'}),TestClient(app) as client:
            response=client.get('/api/latest'); self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()['stale']); self.assertIsNone(response.json()['reading'])
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertEqual(response.headers['x-frame-options'],'DENY')
            self.assertIn("default-src 'self'",response.headers['content-security-policy'])
            self.assertEqual(client.get('/api/history?start=0&end=999999999999').status_code,400)

if __name__=='__main__': unittest.main()
