import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
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

    def test_normalize_errors_and_edge_cases(self):
        self.assertIsNone(aqi(-1))
        with patch('backend.air.BANDS', []):
            self.assertIsNone(aqi(5.0))
        with self.assertRaises(ValueError): normalize({}, {}, 123)
        with self.assertRaises(ValueError): normalize('not-dict', {}, 123)
        with self.assertRaises(ValueError): normalize({'SensorId': 'x'}, {'pm_method': 'unsupported'}, 123)
        with self.assertRaises(ValueError): environment_values(70, 50, 'unknown')
        from backend.air import environment_sql
        with self.assertRaises(ValueError): environment_sql('unknown')
        stale = normalize({'SensorId': 'x', 'DateTime': '2026/01/01T00:00:00Z'}, {}, 2000000000)
        self.assertIn('device_clock_or_stale_source', stale['quality'])
        bad_time = normalize({'SensorId': 'x', 'DateTime': 'invalid-date'}, {}, 123)
        self.assertIn('invalid_device_time', bad_time['quality'])
        single = normalize({'SensorId': 'x', 'pm2_5_cf_1': 10}, {}, 123)
        self.assertIn('single_channel', single['quality'])
        missing = normalize({'SensorId': 'x'}, {}, 123)
        self.assertIn('missing_pm', missing['quality'])
        epa_missing = normalize({'SensorId': 'x', 'pm2_5_cf_1': 10}, {'pm_method': 'epa2021'}, 123)
        self.assertIn('correction_unavailable', epa_missing['quality'])
        self.assertIsNone(epa_missing['pm25'])
        epa_high = normalize({'SensorId': 'x', 'pm2_5_cf_1': 550, 'current_humidity': 50}, {'pm_method': 'epa2021'}, 123)
        self.assertIn('outside_correction_range', epa_high['quality'])
        self.assertIsNone(epa_high['pm25'])

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
    def test_local_backup_is_published_only_after_verification(self):
        real_replace=db.os.replace
        observed=[]
        def publish(source,target):
            source=Path(source); target=Path(target)
            observed.append((source.name,target.name))
            self.assertTrue(source.name.endswith('.partial'))
            self.assertEqual(list(target.parent.glob('*.sqlite')),[])
            return real_replace(source,target)
        with patch('backend.db.os.replace',side_effect=publish):
            snapshot=db.backup()
        self.assertEqual(observed[0][1],snapshot.name)
        self.assertTrue(snapshot.exists())
        self.assertEqual(list(snapshot.parent.glob('*.partial')),[])
    def test_migration_version_advances_in_schema_transaction(self):
        import sqlite3
        migration=db.DATA/'migration.sql'
        migration.write_text('BEGIN IMMEDIATE;\nCREATE TABLE sample (id INTEGER);\nCOMMIT;\n')
        database=db.DATA/'migration-test.sqlite'
        with closing(sqlite3.connect(database)) as con:
            con.executescript(db._versioned_migration(migration,7))
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],7)
            self.assertIsNotNone(con.execute("SELECT name FROM sqlite_master WHERE name='sample'").fetchone())
        broken=db.DATA/'broken.sql'
        broken.write_text('BEGIN IMMEDIATE;\nCREATE TABLE partial (id INTEGER);\nSELECT missing FROM nowhere;\nCOMMIT;\n')
        with closing(sqlite3.connect(database)) as con:
            with self.assertRaises(sqlite3.OperationalError): con.executescript(db._versioned_migration(broken,8))
            con.rollback()
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],7)
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone())
        missing_commit=db.DATA/'missing-commit.sql'
        missing_commit.write_text('BEGIN IMMEDIATE;\nSELECT 1;\n')
        with self.assertRaisesRegex(RuntimeError,'must end with COMMIT'):
            db._versioned_migration(missing_commit,8)
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
        with closing(sqlite3.connect(backups[0])) as con:
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
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        from fastapi.testclient import TestClient
        from backend.app import app
        with patch.dict('os.environ',{'DISABLE_JOBS':'1'}),TestClient(app) as client:
            r=client.get('/api/settings')
            self.assertEqual(r.json()['UNITS'],'imperial')
            headers={'X-Indigo-Request':'1'}
            res=client.post('/api/settings',json={'UNITS':'metric'},headers=headers)
            self.assertEqual(res.status_code,200)
            self.assertEqual(client.get('/api/settings').json()['UNITS'],'metric')
            bad=client.post('/api/settings',json={'UNITS':'kelvin'},headers=headers)
            self.assertEqual(bad.status_code,400)
            import time
            now=int(time.time())//3600*3600
            zone=ZoneInfo('America/Los_Angeles')
            db.set_settings({'timezone':zone.key})
            midnight=datetime.fromtimestamp(now,zone).replace(hour=0,minute=0,second=0,microsecond=0)
            daily_valid=int(midnight.timestamp())
            duplicate_valid=int((midnight+timedelta(hours=17)).timestamp())
            jobs.store_forecasts([
                (now, now+3600, 'weather', 72.0, 50.0, None, 5.0, 20.0, None, 2, 8.5, 74.0, 40.0, None, None, None),
                (now-60, duplicate_valid, 'daily', 70.0, None, None, None, None, None, 3, None, None, None, now+20000, now+60000, 50.0),
                (now, daily_valid, 'daily', 75.0, None, None, None, None, None, 2, None, None, None, now+20000, now+60000, 55.0)
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
            self.assertEqual(client.post('/api/settings',json={'UNITS':'metric'}).status_code,403)

    def test_backup_integrity_failure(self):
        from contextlib import contextmanager
        @contextmanager
        def fake_connect():
            yield Mock()
        mock_target = Mock()
        mock_target.execute.return_value.fetchone.return_value = ['corrupt']
        with patch('backend.db.connect', side_effect=fake_connect), \
             patch('backend.db.sqlite3.connect', return_value=mock_target):
            with self.assertRaises(RuntimeError) as exc:
                db.backup()
            self.assertIn('integrity verification failed', str(exc.exception))
        mock_target.close.assert_called_once()

    def test_health_and_latest_with_reading(self):
        import time
        from fastapi.testclient import TestClient
        from backend.app import app, APP_VERSION
        with patch.dict('os.environ', {'DISABLE_JOBS': '1'}), TestClient(app) as client:
            h = client.get('/api/health')
            self.assertEqual(h.status_code, 200)
            self.assertEqual(h.json(), {'ok': True, 'version': APP_VERSION})
            now = int(time.time())
            with db.connect() as con:
                con.execute("INSERT INTO readings(ts, source_ts, temperature, humidity, temperature_raw, humidity_raw, pm25, method, quality, environment_mode) VALUES (?, '', 75, 45, 80, 40, 350, 'cf1', '', 'purpleair')", (now,))
            latest = client.get('/api/latest').json()
            self.assertFalse(latest['stale'])
            self.assertIsNotNone(latest['reading'])
            self.assertTrue(latest['reading']['beyond_scale'])
            self.assertEqual(latest['reading']['aqi'], 500)
            self.assertEqual(latest['reading']['environment_mode'], 'purpleair')
            # 80 F raw in purpleair mode: 1.0227 * 80 - 9.375 = 72.441
            self.assertAlmostEqual(latest['today_temp_min'], 72.441, places=2)
            self.assertAlmostEqual(latest['today_temp_max'], 72.441, places=2)

            # Insert an earlier reading today with different temp
            with db.connect() as con:
                con.execute("INSERT INTO readings(ts, source_ts, temperature, humidity, temperature_raw, humidity_raw, pm25, method, quality, environment_mode) VALUES (?, '', 60, 50, 65, 50, 20, 'cf1', '', 'purpleair')", (now - 300,))
            latest = client.get('/api/latest').json()
            # 65 F raw: 1.0227 * 65 - 9.375 = 57.1005
            self.assertAlmostEqual(latest['today_temp_min'], 57.1005, places=2)
            self.assertAlmostEqual(latest['today_temp_max'], 72.441, places=2)

    def test_frontend_static_serving_and_404s(self):
        from fastapi.testclient import TestClient
        from backend.app import app
        web_temp = tempfile.TemporaryDirectory()
        web_path = Path(web_temp.name)
        (web_path / 'index.html').write_text('<!doctype html><html><body>SPA</body></html>')
        assets = web_path / 'assets'
        assets.mkdir()
        (assets / 'app.js').write_text('console.log(1);')
        (web_path / 'icon.svg').write_text('<svg></svg>')
        with patch('backend.app.WEB', web_path), patch.dict('os.environ', {'DISABLE_JOBS': '1'}), TestClient(app) as client:
            r_root = client.get('/')
            self.assertEqual(r_root.status_code, 200)
            self.assertIn('SPA', r_root.text)
            r_file = client.get('/icon.svg')
            self.assertEqual(r_file.status_code, 200)
            r_spa = client.get('/overview')
            self.assertEqual(r_spa.status_code, 200)
            self.assertIn('SPA', r_spa.text)
            self.assertEqual(client.get('/missing.png').status_code, 404)
            self.assertEqual(client.get('/api/nonexistent').status_code, 404)
        web_temp.cleanup()

    def test_config_validation_branches(self):
        from backend.config import validate
        with self.assertRaises(ValueError): validate({'forecast_enabled': 'not-a-bool'})
        with self.assertRaises(ValueError): validate({'latitude': 40.0})
        with self.assertRaises(ValueError): validate({'longitude': -100.0})
        with self.assertRaises(ValueError): validate({'sensor_host': '127.0.0.1'})
        with self.assertRaises(ValueError): validate({'sensor_host': '8.8.8.8'})
        with self.assertRaises(ValueError): validate({'sensor_host': '::1'})
        with self.assertRaises(ValueError): validate({'latitude': 95.0, 'longitude': 0.0})
        with self.assertRaises(ValueError): validate({'latitude': 0.0, 'longitude': 200.0})
        with self.assertRaises(ValueError): validate({'latitude': float('nan'), 'longitude': 0.0})
        with self.assertRaises(ValueError): validate({'pm_method': 'invalid'})
        with self.assertRaises(ValueError): validate({'environment_mode': 'invalid'})
        with self.assertRaises(ValueError): validate({'placement': 'invalid'})
        with self.assertRaises(ValueError): validate({'units': 'invalid'})
        with self.assertRaises(Exception): validate({'timezone': 'Invalid/Timezone_Name_X'})

    def test_post_settings_branches(self):
        from fastapi.testclient import TestClient
        from backend.app import app
        with patch.dict('os.environ', {'DISABLE_JOBS': '1'}), TestClient(app) as client:
            client.headers.update({'X-Indigo-Request': '1'})
            self.assertEqual(client.post('/api/settings', json={'FORECAST_LATITUDE': 'bad'}).status_code, 400)
            self.assertEqual(client.post('/api/settings', json={'FORECAST_LATITUDE': '45.0'}).status_code, 400)
            with patch('backend.jobs.weather') as mock_weather:
                res = client.post('/api/settings', json={
                    'FORECAST_ENABLED': 'true',
                    'FORECAST_LATITUDE': '45.0',
                    'FORECAST_LONGITUDE': '-122.0'
                })
                self.assertEqual(res.status_code, 200)
                mock_weather.assert_called_once()

if __name__=='__main__': unittest.main()
