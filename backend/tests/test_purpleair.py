import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from fastapi.testclient import TestClient

from backend import air, app, astronomy, backups, config, db, jobs


class PurpleAirUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_data = db.DATA
        db.DATA = Path(self.tmp.name)
        db.initialize()
        self.client = TestClient(app.app)

    def tearDown(self):
        db.DATA = self.orig_data
        self.tmp.cleanup()

    def test_positive_int_helper(self):
        self.assertEqual(config.positive_int('42'), 42)
        with self.assertRaises(ValueError):
            config.positive_int('0')
        with self.assertRaises(ValueError):
            config.positive_int('-10')
        with self.assertRaises(ValueError):
            config.positive_int('not-a-number')

    def test_config_validation_purpleair(self):
        # Valid PurpleAir API config
        cfg = config.validate({
            'sensor_source': 'purpleair_api',
            'purpleair_sensor_index': '12345',
            'purpleair_api_key': 'key-abc',
            'purpleair_read_key': ' read-key-xyz '
        })
        self.assertEqual(cfg['purpleair_sensor_index'], 12345)
        self.assertEqual(cfg['purpleair_read_key'], 'read-key-xyz')

        # Invalid sensor index
        with self.assertRaises(ValueError):
            config.validate({'purpleair_sensor_index': 0})
        with self.assertRaises(ValueError):
            config.validate({'purpleair_sensor_index': 'bad'})

        # Empty API key
        with self.assertRaises(ValueError):
            config.validate({'purpleair_api_key': '   '})

        # Invalid sensor source
        with self.assertRaises(ValueError):
            config.validate({'sensor_source': 'unknown_source'})

        # PurpleAir API missing API key
        with self.assertRaises(ValueError):
            config.validate({'sensor_source': 'purpleair_api', 'purpleair_sensor_index': 123})
        with self.assertRaises(ValueError):
            config.validate({'purpleair_sensor_index': 123})

    def test_config_import_environment_files(self):
        key_file = Path(self.tmp.name) / 'api_key.txt'
        read_file = Path(self.tmp.name) / 'read_key.txt'
        key_file.write_text('secret-file-key\n', encoding='utf-8')
        read_file.write_text('secret-read-key\n', encoding='utf-8')

        with patch.dict(os.environ, {
            'PURPLEAIR_API_KEY_FILE': str(key_file),
            'PURPLEAIR_READ_KEY_FILE': str(read_file),
            'PURPLEAIR_SENSOR_INDEX': '8888',
        }):
            config.import_environment()
            s = db.settings()
            self.assertEqual(s['purpleair_api_key'], 'secret-file-key')
            self.assertEqual(s['purpleair_read_key'], 'secret-read-key')
            self.assertEqual(s['purpleair_sensor_index'], 8888)

        # Inaccessible/missing key file
        with patch.dict(os.environ, {
            'PURPLEAIR_API_KEY_FILE': str(Path(self.tmp.name) / 'does_not_exist.txt'),
            'PURPLEAIR_SENSOR_INDEX': '8888',
        }):
            with self.assertRaises(ValueError):
                config.import_environment()

    def test_air_normalize_purpleair_api(self):
        now = time.time()
        # Full PurpleAir API format
        api_payload = {
            'sensor': {
                'sensor_index': 12345,
                'pm2.5_cf_1_a': 12.5,
                'pm2.5_cf_1_b': 14.1,
                'temperature': 72.0,
                'humidity': 45.0,
                'last_seen': int(now - 10),
            }
        }
        res = air.normalize(api_payload, {'pm_method': 'cf1'}, now)
        self.assertAlmostEqual(res['pm25'], 13.3)
        self.assertAlmostEqual(res['temperature_raw'], 72.0)
        self.assertAlmostEqual(res['humidity_raw'], 45.0)

        # Fallback fields: data_time_stamp, current_humidity, temperature_a
        alt_payload = {
            'sensor': {
                'sensor_index': 99,
                'pm2.5_cf_1': 8.0,
                'temperature_a': 68.0,
                'humidity_a': 50.0,
            },
            'data_time_stamp': int(now - 30)
        }
        res2 = air.normalize(alt_payload, {'pm_method': 'cf1'}, now)
        self.assertEqual(res2['pm25'], 8.0)
        self.assertEqual(res2['temperature_raw'], 68.0)
        self.assertEqual(res2['humidity_raw'], 50.0)

        # Timestamp overflow handling
        overflow_payload = {
            'sensor': {
                'sensor_index': 100,
                'last_seen': 10**18,
            }
        }
        res3 = air.normalize(overflow_payload, {}, now)
        self.assertEqual(res3['source_ts'], '')

        # Invalid device time string handling
        bad_time_payload = {
            'sensor': {
                'sensor_index': 100,
                'DateTime': 'not-a-valid-timestamp',
            }
        }
        res4 = air.normalize(bad_time_payload, {}, now)
        self.assertIn('invalid_device_time', res4['quality'])

        # Invalid payload structures
        with self.assertRaises(ValueError):
            air.normalize("string not dict", {}, now)
        with self.assertRaises(ValueError):
            air.normalize({'some_other': 'data'}, {}, now)

    def test_jobs_collect_purpleair_missing_config(self):
        # 1. No sensor configured (hits fallback when source is empty and neither index nor host set)
        db.set_settings({'sensor_source': '', 'sensor_host': '', 'purpleair_sensor_index': None})
        asyncio.run(jobs.collect())
        with db.connect() as con:
            st = con.execute("SELECT * FROM job_status WHERE name='sensor'").fetchone()
            self.assertIn('Sensor is not configured', st['error'])

        # Auto-detect purpleair_api when sensor_source is not explicitly set (hits line 24)
        db.set_settings({'sensor_source': None, 'sensor_host': None, 'purpleair_sensor_index': 555, 'purpleair_api_key': 'key'})
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'sensor': {'sensor_index': 555, 'pm2.5_cf_1': 5.0, 'temperature': 70.0, 'humidity': 40.0}}
        with patch('httpx.AsyncClient.get', AsyncMock(return_value=mock_resp)):
            asyncio.run(jobs.collect())
            with db.connect() as con:
                st = con.execute("SELECT * FROM job_status WHERE name='sensor'").fetchone()
                self.assertIsNone(st['error'])

        # 2. Missing sensor index in purpleair_api mode
        db.set_settings({'sensor_source': 'purpleair_api', 'purpleair_sensor_index': None})
        asyncio.run(jobs.collect())
        with db.connect() as con:
            st = con.execute("SELECT * FROM job_status WHERE name='sensor'").fetchone()
            self.assertIn('sensor index is not configured', st['error'])

        # 3. Missing API key in purpleair_api mode
        db.set_settings({
            'sensor_source': 'purpleair_api',
            'purpleair_sensor_index': 12345,
            'purpleair_api_key': None
        })
        asyncio.run(jobs.collect())
        with db.connect() as con:
            st = con.execute("SELECT * FROM job_status WHERE name='sensor'").fetchone()
            self.assertIn('API key is not configured', st['error'])

        # 4. Local mode with missing host
        db.set_settings({'sensor_source': 'local', 'sensor_host': None})
        asyncio.run(jobs.collect())
        with db.connect() as con:
            st = con.execute("SELECT * FROM job_status WHERE name='sensor'").fetchone()
            self.assertIn('Sensor is not configured', st['error'])

    def test_jobs_collect_purpleair_api_success_and_errors(self):
        db.set_settings({
            'sensor_source': 'purpleair_api',
            'purpleair_sensor_index': 777,
            'purpleair_api_key': 'test-key',
            'purpleair_read_key': 'test-read-key',
        })

        # Test 403 Forbidden / Invalid API key
        mock_resp_403 = MagicMock()
        mock_resp_403.status_code = 403
        with patch('httpx.AsyncClient.get', AsyncMock(return_value=mock_resp_403)):
            asyncio.run(jobs.collect())
            with db.connect() as con:
                st = con.execute("SELECT * FROM job_status WHERE name='sensor'").fetchone()
                self.assertIn('PurpleAir API key is invalid or unauthorized', st['error'])

        # Test 404 Not Found
        mock_resp_404 = MagicMock()
        mock_resp_404.status_code = 404
        with patch('httpx.AsyncClient.get', AsyncMock(return_value=mock_resp_404)):
            asyncio.run(jobs.collect())
            with db.connect() as con:
                st = con.execute("SELECT * FROM job_status WHERE name='sensor'").fetchone()
                self.assertIn('PurpleAir sensor 777 was not found', st['error'])

        # Test Success 200
        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.json.return_value = {
            'sensor': {
                'sensor_index': 777,
                'pm2.5_cf_1_a': 10.0,
                'pm2.5_cf_1_b': 10.2,
                'temperature': 70.0,
                'humidity': 40.0,
                'last_seen': int(time.time()),
            }
        }
        with patch('httpx.AsyncClient.get', AsyncMock(return_value=mock_resp_200)):
            asyncio.run(jobs.collect())
            with db.connect() as con:
                st = con.execute("SELECT * FROM job_status WHERE name='sensor'").fetchone()
                self.assertIsNone(st['error'])
                reading = con.execute("SELECT * FROM readings").fetchone()
                self.assertIsNotNone(reading)
                self.assertAlmostEqual(reading['pm25'], 10.1)

    def test_jobs_collect_purpleair_key_from_files(self):
        key_file = Path(self.tmp.name) / 'api_file.txt'
        read_file = Path(self.tmp.name) / 'read_file.txt'
        key_file.write_text('key-from-file', encoding='utf-8')
        read_file.write_text('read-from-file', encoding='utf-8')

        db.set_settings({
            'sensor_source': 'purpleair_api',
            'purpleair_sensor_index': 888,
            'purpleair_api_key': None,
            'purpleair_read_key': None,
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'sensor': {
                'sensor_index': 888,
                'pm2.5_cf_1': 5.0,
                'temperature': 65.0,
                'humidity': 35.0,
                'last_seen': int(time.time()),
            }
        }

        with patch.dict(os.environ, {
            'PURPLEAIR_API_KEY_FILE': str(key_file),
            'PURPLEAIR_READ_KEY_FILE': str(read_file),
        }):
            with patch('httpx.AsyncClient.get', AsyncMock(return_value=mock_resp)):
                asyncio.run(jobs.collect())
                with db.connect() as con:
                    st = con.execute("SELECT * FROM job_status WHERE name='sensor'").fetchone()
                    self.assertIsNone(st['error'])

        # Test OSError handling when key file and read key file are invalid paths
        with patch.dict(os.environ, {
            'PURPLEAIR_API_KEY_FILE': str(Path(self.tmp.name) / 'nonexistent_key_file'),
            'PURPLEAIR_READ_KEY_FILE': str(Path(self.tmp.name) / 'nonexistent_read_file'),
        }):
            asyncio.run(jobs.collect())
            with db.connect() as con:
                st = con.execute("SELECT * FROM job_status WHERE name='sensor'").fetchone()
                self.assertIn('API key is not configured', st['error'])

    def test_jobs_collect_purpleair_read_key_file_oserror(self):
        db.set_settings({
            'sensor_source': 'purpleair_api',
            'purpleair_sensor_index': 777,
            'purpleair_api_key': 'valid-api-key',
            'purpleair_read_key': None,
        })
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'sensor': {'sensor_index': 777, 'pm2.5_cf_1': 4.0, 'temperature': 65.0, 'humidity': 45.0}}
        with patch.dict(os.environ, {
            'PURPLEAIR_READ_KEY_FILE': str(Path(self.tmp.name) / 'nonexistent_read_key'),
        }):
            with patch('httpx.AsyncClient.get', AsyncMock(return_value=mock_resp)):
                asyncio.run(jobs.collect())
                with db.connect() as con:
                    st = con.execute("SELECT * FROM job_status WHERE name='sensor'").fetchone()
                    self.assertIsNone(st['error'])

    def test_jobs_collect_purpleair_retry_loop(self):
        db.set_settings({
            'sensor_source': 'purpleair_api',
            'purpleair_sensor_index': 999,
            'purpleair_api_key': 'retry-key',
        })
        with patch('asyncio.sleep', AsyncMock()):
            with patch('httpx.AsyncClient.get', AsyncMock(side_effect=httpx.ConnectError('simulated connect error'))):
                with self.assertRaises(httpx.ConnectError):
                    asyncio.run(jobs.collect())

    def test_api_settings_purpleair_get_and_post(self):
        db.set_settings({
            'sensor_source': 'purpleair_api',
            'purpleair_sensor_index': 12345,
            'purpleair_api_key': 'secret-key-hidden',
            'purpleair_read_key': 'secret-read-hidden',
        })
        res = self.client.get('/api/settings')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['SENSOR_SOURCE'], 'purpleair_api')
        self.assertEqual(data['PURPLEAIR_SENSOR_INDEX'], 12345)
        self.assertTrue(data['HAS_PURPLEAIR_API_KEY'])
        self.assertTrue(data['HAS_PURPLEAIR_READ_KEY'])
        # Ensure raw keys are NEVER exposed
        self.assertNotIn('secret-key-hidden', res.text)
        self.assertNotIn('secret-read-hidden', res.text)

        # Test POST settings with PurpleAir fields
        with patch('backend.jobs.collect', AsyncMock()) as mock_collect:
            post_res = self.client.post('/api/settings', headers={'X-Indigo-Request': '1'}, json={
                'SENSOR_SOURCE': 'purpleair_api',
                'PURPLEAIR_SENSOR_INDEX': '54321',
                'PURPLEAIR_API_KEY': 'new-key',
                'PURPLEAIR_READ_KEY': 'new-read-key',
            })
            self.assertEqual(post_res.status_code, 200)
            mock_collect.assert_called()

        s = db.settings()
        self.assertEqual(s['purpleair_sensor_index'], 54321)
        self.assertEqual(s['purpleair_api_key'], 'new-key')

    def test_astronomy_gaps_and_stargazing_branches(self):
        # 1. _norm_rad
        import math; self.assertAlmostEqual(astronomy._norm_rad(5 * math.pi), math.pi)

        # 2. zodiac_sign edge case (360.0 degrees)
        self.assertEqual(astronomy.get_constellation(360.0, 0.0), 'Pisces')

        # 3. First quarter moon phase
        first_q_details = astronomy.moon_position_details(1715756000.0, 37.77, -122.42)
        self.assertEqual(first_q_details['phase_code'], 'first_quarter')
        self.assertEqual(first_q_details['phase_name'], 'First Quarter')

        # 4. Sun twilight polar night / midnight sun
        polar_twilight = astronomy.compute_twilight_times(1718985600, 85.0, 0.0)
        self.assertIsNone(polar_twilight['astronomical_dusk'])

        # 5. Meteor showers peak alt < 5.0 (skipped radiants from polar latitude)
        showers_south_pole = astronomy.compute_meteor_showers(1718985600, -85.0)
        self.assertTrue(len(showers_south_pole) < 8)

        # 6. Stargazing forecast moon branches
        # Moon visible with illumination < 25
        sg0 = astronomy.compute_stargazing_forecast(
            {'altitude_deg': 20.0, 'illumination_pct': 10.0},
            {'darkness_hours': 6.0},
            cloud_cover=10.0
        )
        self.assertIn('thin crescent', sg0['moon_interference'])

        # Moon visible with illumination between 25 and 60
        sg1 = astronomy.compute_stargazing_forecast(
            {'altitude_deg': 20.0, 'illumination_pct': 40.0},
            {'darkness_hours': 6.0},
            cloud_cover=35.0
        )
        self.assertIn('moderate moonlight', sg1['moon_interference'])

        # Moon visible with illumination >= 60
        sg2 = astronomy.compute_stargazing_forecast(
            {'altitude_deg': 20.0, 'illumination_pct': 80.0},
            {'darkness_hours': 6.0}
        )
        self.assertIn('bright moon', sg2['moon_interference'])

        # Cloud cover 40-70% and Fair score
        sg3 = astronomy.compute_stargazing_forecast(
            {'altitude_deg': 20.0, 'illumination_pct': 40.0},
            {'darkness_hours': 0.0},
            cloud_cover=60.0
        )
        self.assertEqual(sg3['rating'], 'Fair')

        # Cloud cover > 70% and Poor score
        sg4 = astronomy.compute_stargazing_forecast(
            {'altitude_deg': 30.0, 'illumination_pct': 90.0},
            {'darkness_hours': 0.0},
            cloud_cover=95.0
        )
        self.assertEqual(sg4['rating'], 'Poor')

    def test_app_cache_control_and_astronomy_cloud_cover_exception(self):
        # Asset cache control
        res = self.client.get('/assets/test-bundle.js')
        self.assertEqual(res.headers.get('cache-control'), 'public, max-age=31536000, immutable')

        # Astronomy endpoint handles exception in cloud cover query gracefully
        db.set_settings({'latitude': 37.77, 'longitude': -122.42})
        class FailingCon:
            def execute(self, *a, **k):
                raise RuntimeError('simulated error')
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        orig_connect = db.connect
        call_count = 0
        def mock_connect():
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                return FailingCon()
            return orig_connect()

        with patch('backend.app.db.connect', side_effect=mock_connect):
            res = self.client.get('/api/astronomy')
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data['available'])
            self.assertIsNone(data['stargazing']['cloud_cover_pct'])

        # Now test with a real forecast cloud cover row in the db (hits line 335)
        now = int(time.time())
        jobs.store_forecasts([(now, now + 1800, 'weather', 72.0, 45.0, 10.0, 3.0, 10.0, 45.0, 0, 5.0, 72.0, 25.0, None, None, None)])
        res2 = self.client.get('/api/astronomy')
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2['stargazing']['cloud_cover_pct'], 25.0)


if __name__ == '__main__':
    unittest.main()
