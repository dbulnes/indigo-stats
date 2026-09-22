import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

from backend import db, jobs


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(db, 'DATA', Path(self.temp.name))
        self.patch.start()
        db.initialize()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_collect_success_and_retries(self):
        async def run():
            db.set_settings({'sensor_host': '192.168.1.100'})
            sample_payload = {
                'SensorId': 'synthetic',
                'current_temp_f': 75.0,
                'current_humidity': 40.0,
                'pm2_5_cf_1': 10.0,
                'pm2_5_cf_1_b': 10.5,
                'DateTime': '2026/09/21 12:00:00',
            }
            mock_resp = Mock()
            mock_resp.json.return_value = sample_payload
            mock_resp.raise_for_status = Mock()

            with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get:
                mock_get.return_value = mock_resp
                await jobs.collect()
                mock_get.assert_called_once_with('http://192.168.1.100/json')

            with db.connect() as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM readings').fetchone()[0], 1)
                self.assertIsNotNone(con.execute("SELECT last_success FROM job_status WHERE name='sensor'").fetchone()[0])

            with patch('httpx.AsyncClient.get', new_callable=AsyncMock, side_effect=httpx.ConnectError('fail')), patch('asyncio.sleep', new_callable=AsyncMock):
                with self.assertRaises(httpx.ConnectError):
                    await jobs.collect()

        asyncio.run(run())

    def test_weather_success_and_storage(self):
        async def run():
            db.set_settings({
                'forecast_enabled': True,
                'latitude': 37.77,
                'longitude': -122.42,
            })
            weather_data = {
                'hourly': {
                    'time': [1000, 4600],
                    'temperature_2m': [65.0, 68.0],
                    'relative_humidity_2m': [55.0, 50.0],
                    'uv_index': [3.0, 5.0],
                    'precipitation_probability': [10.0, 0.0],
                    'weather_code': [1, 2],
                    'wind_speed_10m': [7.0, 9.0],
                    'apparent_temperature': [64.0, 67.0],
                    'cloud_cover': [20.0, 15.0],
                },
                'daily': {
                    'time': [1000],
                    'weather_code': [1],
                    'sunrise': [1200],
                    'sunset': [3000],
                    'temperature_2m_max': [70.0],
                    'temperature_2m_min': [52.0],
                },
            }
            air_data = {
                'hourly': {
                    'time': [1000, 4600],
                    'pm2_5': [8.0, 12.0],
                    'us_aqi': [33.0, 50.0],
                }
            }

            async def mock_get(url, params=None):
                mock_resp = Mock()
                mock_resp.raise_for_status = Mock()
                if 'air-quality' in url:
                    mock_resp.json.return_value = air_data
                else:
                    mock_resp.json.return_value = weather_data
                return mock_resp

            with patch('httpx.AsyncClient.get', side_effect=mock_get):
                await jobs.weather()

            with db.connect() as con:
                self.assertIsNotNone(con.execute("SELECT last_success FROM job_status WHERE name='weather'").fetchone()[0])
                count = con.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
                self.assertGreater(count, 0)
                daily = con.execute("SELECT * FROM forecasts WHERE kind='daily'").fetchone()
                self.assertEqual(daily['temp_min'], 52.0)
                self.assertEqual(daily['sunrise'], 1200)

            # Test forecast_enabled=True but missing latitude/longitude
            db.set_settings({'forecast_enabled': True, 'latitude': None, 'longitude': None})
            # Remove keys from settings
            with db.connect() as con:
                con.execute("DELETE FROM settings WHERE key IN ('latitude', 'longitude')")
            await jobs.weather()
            with db.connect() as con:
                err = con.execute("SELECT error FROM job_status WHERE name='weather'").fetchone()[0]
                self.assertEqual(err, 'Forecast location is not configured')

        asyncio.run(run())

    def test_loop_task_execution_and_cancellation(self):
        async def run():
            called = []
            async def fast_task():
                called.append(1)
                raise asyncio.CancelledError()

            with self.assertRaises(asyncio.CancelledError):
                await jobs.loop('fast', fast_task, 1)
            self.assertEqual(len(called), 1)

            attempts = 0
            async def failing_task():
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError('temporary loop error')
                raise asyncio.CancelledError()

            with patch('asyncio.sleep', new_callable=AsyncMock):
                with self.assertRaises(asyncio.CancelledError):
                    await jobs.loop('failing', failing_task, 1)

            with db.connect() as con:
                error = con.execute("SELECT error FROM job_status WHERE name='failing'").fetchone()[0]
                self.assertIn('RuntimeError: job failed; will retry', error)

            # Test loop calling maintenance (sync function)
            sync_called = []
            def sync_task():
                sync_called.append(1)
                raise asyncio.CancelledError()

            with self.assertRaises(asyncio.CancelledError):
                await jobs.loop('maintenance', sync_task, 1)
            self.assertEqual(len(sync_called), 1)

        asyncio.run(run())


if __name__ == '__main__': unittest.main()
