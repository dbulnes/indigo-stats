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
    def test_template_private_defaults_are_empty(self):
        import xml.etree.ElementTree as ET
        template=ET.parse(Path(__file__).parents[2]/'templates'/'indigo-stats.xml').getroot()
        fields={e.attrib['Target']:e for e in template.findall('Config')}
        for key in ('SENSOR_HOST','FORECAST_LATITUDE','FORECAST_LONGITUDE','LOCATION_ADDRESS'):
            self.assertFalse(fields[key].text)
        self.assertEqual(fields['FORECAST_ENABLED'].text,'false')
        self.assertEqual(template.findtext('Privileged'),'false')
    def test_v1_migration_preserves_raw_readings_and_backup(self):
        import sqlite3
        with db.connect() as con:
            con.execute("INSERT INTO readings(ts,temperature_raw,humidity_raw,method,quality) VALUES(60,81,42,'cf1','')")
            con.execute('ALTER TABLE readings DROP COLUMN environment_mode')
            con.execute('PRAGMA user_version=1')
        db.initialize()
        with db.connect() as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],2)
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
        jobs.store_forecasts([(100,200,'air',None,None,5),(199,200,'air',None,None,6),(201,200,'air',None,None,50)])
        with db.connect() as con:
            self.assertEqual(forecast_rows(con,200,201)[0]['pm25'],6)
            self.assertEqual(forecast_rows(con,200,201,False)[0]['pm25'],50)
    def test_api_never_exposes_private_config(self):
        from fastapi.testclient import TestClient
        from backend.app import app
        db.set_settings({'address':'PRIVATE ADDRESS','latitude':12.345,'longitude':67.89,'timezone':'America/Los_Angeles'})
        with patch.dict('os.environ',{'DISABLE_JOBS':'1'}),TestClient(app) as client:
            for url in ['/api/status','/api/latest','/api/history?start=0&end=86400','/api/forecast']:
                r=client.get(url); self.assertEqual(r.status_code,200)
                self.assertNotIn('PRIVATE ADDRESS',r.text); self.assertNotIn('12.345',r.text)
            self.assertIn(client.post('/api/settings',json={}).status_code,(404,405))
            self.assertEqual(client.get('/api/history?start=10&end=5').status_code,400)

if __name__=='__main__': unittest.main()
