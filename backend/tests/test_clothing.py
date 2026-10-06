import copy
from contextlib import closing
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient

from backend import clothing, db, jobs
from backend.app import app


def response():
    answers = {}
    for group, options in clothing.CHOICES.items():
        probabilities = {str(i): (0.8 if i == 1 else 0.2 / (len(options)-1)) for i in range(len(options))}
        answers[group] = {'type': 'choice', 'choice': '1', 'probabilities': probabilities, 'confidence': 0.7}
    answers.update({item: {'type': 'noul', 'noul': 0.6} for item in clothing.ACCESSORIES})
    return {'model': 'jev-test', 'answers': answers, 'usage': {'input_tokens': 100, 'output_tokens': 20}}


class ClothingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(db, 'DATA', Path(self.temp.name).resolve())
        self.patch.start()
        self.addCleanup(self.patch.stop)
        db.initialize()
        db.set_settings({'forecast_enabled': True, 'timezone': 'Etc/UTC', 'placement': 'outdoors',
                         'latitude': 0, 'longitude': 0, 'sensor_host': 'private.invalid'})
        environment = patch.dict(os.environ, {'CLOTHING_JEVMODEL_API_KEY': 'synthetic-test-key',
                                             'CLOTHING_TYPESAFE_API_KEY': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.now = int(datetime(2026, 1, 12, 8, 30, tzinfo=ZoneInfo('Etc/UTC')).timestamp())
        self.seed()
        self.client = TestClient(app)

    def seed(self, zone='Etc/UTC'):
        local = datetime.fromtimestamp(self.now, ZoneInfo(zone))
        start = int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        rows = [(self.now-60, start+hour*3600, 'weather', 50, 60, None, None, 40, None,
                 None, 10, 45) for hour in range(48)]
        jobs.store_forecasts(rows, db.settings())
        with db.connect() as con:
            con.execute('DELETE FROM readings')
            con.execute('''INSERT INTO readings
                (ts,source_ts,temperature_raw,humidity_raw,method,quality)
                VALUES (?,?,?,?,'epa','')''', (self.now-30,
                    datetime.fromtimestamp(self.now-30, ZoneInfo('Etc/UTC')).isoformat(), 60, 40))
        db.set_settings({'clothing_sensor_source': {'context': jobs.sensor_context(db.settings()), 'ts': self.now-30}})

    def generate(self):
        with patch.object(clothing.time, 'time', return_value=self.now):
            return clothing.generate({'provider': 'jevmodel'})

    def test_manual_generation_reuses_persistent_cache_and_reports_usage(self):
        with patch.object(clothing, 'ask', return_value=response()) as ask:
            result = self.generate()
            self.assertEqual(ask.call_count, 3)
            self.assertFalse(result['busy'])
            self.assertEqual(result['usage'], {'input_tokens': 300, 'output_tokens': 60})
            self.generate()
            clothing.public_status(self.now)
            self.assertEqual(ask.call_count, 3)
            wire = json.dumps([call.args[2] for call in ask.call_args_list])
            public = json.dumps(result)
            for private in ('latitude', 'longitude', 'private.invalid', 'synthetic-test-key',
                            'sensor_index', 'source_ts', 'evidence', 'context'):
                self.assertNotIn(private, wire)
                self.assertNotIn(private, public)
            self.assertIn('PurpleAir current observation', wire)
            self.assertIn('assumptions', wire)
            self.assertEqual(result['results'][0]['judgments']['bottoms'][0]['label'], 'Lightweight trousers')
            self.assertEqual(result['results'][0]['judgments']['umbrella'], 0.6)
        with closing(sqlite3.connect(db.backup())) as backup:
            self.assertNotIn('synthetic-test-key', '\n'.join(backup.iterdump()))

    def test_completed_periods_and_fractional_timezone(self):
        self.now += 9*3600
        self.seed()
        self.assertEqual([s['period'] for s in clothing.weather_states(self.now, db.settings())], ['evening'])
        self.now += 6*3600
        self.seed()
        self.assertEqual(len(clothing.weather_states(self.now, db.settings())), 3)
        db.set_settings({'timezone': 'Asia/Kolkata'})
        self.now = int(datetime(2026, 1, 12, 8, 30, tzinfo=ZoneInfo('Asia/Kolkata')).timestamp())
        self.seed('Asia/Kolkata')
        self.assertEqual(len(clothing.weather_states(self.now, db.settings())), 3)

    def test_overnight_calendar_windows_and_selected_forecasts(self):
        zone = ZoneInfo('America/Los_Angeles')
        db.set_settings({'timezone': zone.key})
        cases = [
            ('2026-10-05T22:59:59', '2026-10-05', ['evening']),
            ('2026-10-05T23:00:00', '2026-10-06', ['morning', 'afternoon', 'evening']),
            ('2026-10-05T23:44:00', '2026-10-06', ['morning', 'afternoon', 'evening']),
            ('2026-10-06T00:00:00', '2026-10-06', ['morning', 'afternoon', 'evening']),
            ('2026-10-06T05:59:59', '2026-10-06', ['morning', 'afternoon', 'evening']),
            ('2026-10-06T06:00:00', '2026-10-06', ['morning', 'afternoon', 'evening']),
            ('2026-01-31T23:44:00', '2026-02-01', ['morning', 'afternoon', 'evening']),
            ('2026-12-31T23:44:00', '2027-01-01', ['morning', 'afternoon', 'evening']),
            ('2026-03-07T23:44:00', '2026-03-08', ['morning', 'afternoon', 'evening']),
            ('2026-10-31T23:44:00', '2026-11-01', ['morning', 'afternoon', 'evening']),
            ('2026-11-01T01:30:00', '2026-11-01', ['morning', 'afternoon', 'evening']),
        ]
        for instant, target, names in cases:
            with self.subTest(instant=instant):
                self.now = int(datetime.fromisoformat(instant).replace(tzinfo=zone).timestamp())
                self.seed(zone.key)
                midnight = int(datetime.fromisoformat(target).replace(tzinfo=zone).timestamp())
                with db.connect() as con:
                    con.execute('UPDATE forecasts SET temperature=75 WHERE valid>=?', (midnight,))
                states = clothing.weather_states(self.now, db.settings())
                self.assertEqual([s['period'] for s in states], names)
                for state in states:
                    name, start, end = next(p for p in clothing.PERIODS if p[0] == state['period'])
                    self.assertEqual(datetime.fromtimestamp(state['start'], zone).isoformat(),
                                     datetime.fromisoformat(f'{target}T{start:02}:00:00').replace(tzinfo=zone).isoformat())
                    self.assertEqual(datetime.fromtimestamp(state['end'], zone).hour, end)
                    self.assertEqual(datetime.fromtimestamp(state['end'], zone).date().isoformat(), target)
                    self.assertEqual(state['state']['forecast']['temperature_f'], [75, 75])
                    self.assertEqual(len(clothing.QUESTIONS), 8)
                    if len(names) == 3:
                        self.assertEqual(state['state']['forecast']['local_hours'], f'{start:02}:00–{end:02}:00')

    def test_overnight_missing_next_day_fails_without_paid_call(self):
        self.now = int(datetime(2026, 10, 5, 23, 44, tzinfo=ZoneInfo('Etc/UTC')).timestamp())
        self.seed()
        with db.connect() as con:
            con.execute('DELETE FROM forecasts WHERE valid>=?', (self.now + 16*60,))
        with patch.object(clothing, 'ask') as ask:
            with self.assertRaisesRegex(clothing.ClothingError, 'complete'):
                self.generate()
            ask.assert_not_called()

    def test_overnight_cache_survives_midnight_but_is_date_isolated(self):
        zone = ZoneInfo('America/Los_Angeles')
        db.set_settings({'timezone': zone.key, 'placement': 'indoors'})
        self.now = int(datetime(2026, 10, 5, 23, 44, tzinfo=zone).timestamp())
        self.seed(zone.key)
        with patch.object(clothing, 'ask', return_value=response()) as ask:
            overnight = self.generate()
            self.assertEqual(ask.call_count, 3)
            self.now += 20*60
            midnight = self.generate()
            self.assertEqual(ask.call_count, 3)
            self.assertEqual(midnight['results'], overnight['results'])
            self.assertFalse(any(r['stale'] for r in midnight['results']))
            next_states = clothing.weather_states(self.now, db.settings())
            cached = db.settings()['clothing_state']['results'][0]
            wrong_date = dict(cached, start=cached['start']-86400)
            self.assertFalse(clothing.valid_cache(wrong_date, next_states[0], self.now, 'jevmodel'))
            self.now += 24*3600
            self.seed(zone.key)
            self.generate()
            self.assertEqual(ask.call_count, 6)

    def test_missing_stale_disabled_forecasts_fail_before_spending(self):
        with patch.object(clothing, 'ask') as ask:
            for sql in ("UPDATE forecasts SET fetched=fetched-10800",
                        "UPDATE forecasts SET humidity=NULL",
                        "DELETE FROM forecasts WHERE valid%86400=79200"):
                self.seed()
                with db.connect() as con:
                    con.execute(sql)
                with self.assertRaises(clothing.ClothingError):
                    self.generate()
            db.set_settings({'forecast_enabled': False})
            with self.assertRaises(clothing.ClothingError):
                self.generate()
            ask.assert_not_called()

    def test_missing_stale_and_indoor_sensor_fall_back_explicitly(self):
        for sql in ('UPDATE readings SET ts=ts-181', "UPDATE readings SET source_ts='2020-01-01T00:00:00Z'",
                    'UPDATE readings SET source_ts=NULL', 'DELETE FROM readings'):
            self.seed()
            with db.connect() as con:
                con.execute(sql)
            states = clothing.weather_states(self.now, db.settings())
            self.assertIsNone(states[0]['state']['current_observation'])
            self.assertIn('forecasts only', states[0]['warning'])
        self.seed()
        db.set_settings({'placement': 'indoors'})
        self.assertIsNone(clothing.weather_states(self.now, db.settings())[0]['state']['current_observation'])

    def test_partial_failure_and_usage_survive(self):
        with patch.object(clothing, 'ask', side_effect=[response(), clothing.ClothingError('Provider failed.', 502)]):
            with self.assertRaises(clothing.ClothingError):
                self.generate()
        status = clothing.public_status(self.now)
        self.assertEqual(len(status['results']), 1)
        self.assertEqual(status['usage']['input_tokens'], 100)
        self.assertEqual(status['unknown_usage_requests'], 1)
        with patch.object(clothing, 'ask') as ask:
            with self.assertRaisesRegex(clothing.ClothingError, 'five minutes'):
                self.generate()
            ask.assert_not_called()

    def test_invalid_response_retains_reported_usage(self):
        value = response()
        del value['answers']['gloves']
        with patch.object(clothing, 'ask', return_value=value):
            with self.assertRaises(clothing.ClothingError):
                self.generate()
        status = clothing.public_status(self.now)
        self.assertEqual(status['usage']['input_tokens'], 100)
        self.assertEqual(status['unknown_usage_requests'], 0)
        self.assertEqual(status['results'], [])

    def test_unknown_usage_and_known_unbilled(self):
        with patch.object(clothing, 'ask', side_effect=clothing.ClothingError('Rejected.', 502, unbilled=True)):
            with self.assertRaises(clothing.ClothingError):
                self.generate()
        self.assertEqual(clothing.public_status(self.now)['unknown_usage_requests'], 0)
        self.now += 301
        self.seed()
        value = response()
        value['usage'] = {'input_tokens': 100}
        with patch.object(clothing, 'ask', return_value=value):
            with self.assertRaises(clothing.ClothingError):
                self.generate()
        self.assertEqual(clothing.public_status(self.now)['unknown_usage_requests'], 1)
        self.assertEqual(clothing.public_status(self.now)['usage']['input_tokens'], 100)

    def test_strict_validation(self):
        mutations = [
            lambda v: v['answers']['bottoms']['probabilities'].update({'0': float('nan')}),
            lambda v: v['answers']['bottoms']['probabilities'].update({'0': True}),
            lambda v: v['answers']['bottoms']['probabilities'].update({'unknown': 0}),
            lambda v: v['answers']['bottoms'].update(choice='0'),
            lambda v: v['answers']['gloves'].update(noul=1.1),
            lambda v: v['answers']['gloves'].update(noul=10**1000),
            lambda v: v['answers']['bottoms']['probabilities'].update({'0': 10**1000}),
            lambda v: v['answers']['gloves'].update(type='choice'),
            lambda v: v['usage'].update(input_tokens=-1),
            lambda v: v['usage'].update(input_tokens=10**1000),
            lambda v: v.update(answers=[]),
        ]
        for mutation in mutations:
            invalid = copy.deepcopy(response())
            mutation(invalid)
            with self.assertRaises(clothing.ClothingError):
                clothing.validate_response(invalid)

    def test_oversized_usage_never_corrupts_persistent_totals(self):
        value = response()
        value['usage']['input_tokens'] = 10**1000
        with patch.object(clothing, 'ask', return_value=value):
            with self.assertRaises(clothing.ClothingError):
                self.generate()
        status = clothing.public_status(self.now)
        self.assertEqual(status['usage'], {'input_tokens': 0, 'output_tokens': 20})
        self.assertEqual(status['unknown_usage_requests'], 1)
        self.assertNotIn(str(10**1000), json.dumps(db.settings()))

    def test_environment_credentials_and_provider_isolation(self):
        self.assertEqual(clothing.read_key('jevmodel'), 'synthetic-test-key')
        with self.assertRaises(clothing.ClothingError):
            clothing.read_key('typesafe')
        with patch.dict(os.environ, {'CLOTHING_TYPESAFE_API_KEY': ' typesafe-test-key\n'}):
            self.assertEqual(clothing.read_key('typesafe'), 'typesafe-test-key')
            clothing.configure({'provider': 'typesafe'})
            self.assertTrue(clothing.public_status(self.now)['configured'])
        with self.assertRaises(clothing.ClothingError):
            clothing.configure({'provider': []})

    def test_invalid_environment_keys_never_leak_or_fall_back(self):
        for value in ('', ' ', 'private test key', 'private\nkey', 'é', 'x'*513):
            with patch.dict(os.environ, {'CLOTHING_JEVMODEL_API_KEY': value}), patch.object(clothing, 'ask') as ask:
                self.assertFalse(clothing.public_status(self.now)['configured'])
                with self.assertRaisesRegex(clothing.ClothingError, 'container environment'):
                    self.generate()
                ask.assert_not_called()
        with self.assertRaises(clothing.ClothingError):
            clothing.configure({'provider': 'jevmodel', 'api_key': 'never-persist-this'})
        self.assertNotIn('never-persist-this', json.dumps(db.settings()))

    def test_routes_csrf_no_get_spending_concurrency(self):
        with patch.object(clothing, 'ask') as ask:
            self.assertEqual(self.client.get('/api/clothing').status_code, 200)
            self.assertEqual(self.client.post('/api/clothing/generate').status_code, 403)
            self.assertEqual(self.client.put('/api/clothing', json={'provider': 'typesafe'}).status_code, 403)
            with clothing.LOCK:
                self.assertEqual(self.client.post('/api/clothing/generate',
                                                 json={'provider': 'jevmodel'},
                                                 headers={'X-Indigo-Request': '1'}).status_code, 409)
            ask.assert_not_called()

    def test_generation_is_bound_to_reviewed_provider(self):
        headers = {'X-Indigo-Request': '1'}
        with patch.object(clothing, 'ask') as ask:
            clothing.configure({'provider': 'typesafe'})
            changed = self.client.post('/api/clothing/generate', json={'provider': 'jevmodel'}, headers=headers)
            self.assertEqual(changed.status_code, 409)
            self.assertIn('selected provider changed', changed.json()['detail'])
            self.assertNotIn('clothing_state', db.settings())
            self.assertEqual(self.client.post('/api/clothing/generate', headers=headers).status_code, 422)
            for payload in ({}, {'provider': []}, {'provider': 'other'},
                            {'provider': 'typesafe', 'api_key': 'never-persist-this'}):
                self.assertEqual(self.client.post('/api/clothing/generate', json=payload,
                                                 headers=headers).status_code, 400)
            ask.assert_not_called()

    def test_adapter_contract_and_sanitized_errors(self):
        real_client = httpx.AsyncClient
        seen = []
        def handler(request):
            seen.append(request)
            body = json.loads(request.content)
            self.assertEqual(body['model'], 'jev-latest')
            self.assertEqual(sum(q['type'] == 'choice' for q in body['questions'].values()), 5)
            self.assertEqual(sum(q['type'] == 'noul' for q in body['questions'].values()), 3)
            return httpx.Response(200, json=response())
        with patch.object(clothing.httpx, 'AsyncClient', side_effect=lambda **kw: real_client(
                transport=httpx.MockTransport(handler), **kw)):
            clothing.ask('jevmodel', 'synthetic-test-key', {'forecast': {}}, 'test-id')
        self.assertEqual(str(seen[0].url), 'https://jevmodel.org/v1/systemone')
        self.assertEqual(seen[0].headers['Idempotency-Key'], 'test-id')
        for code in (401, 402, 429, 502):
            with patch.object(clothing.httpx, 'AsyncClient', side_effect=lambda **kw: real_client(
                    transport=httpx.MockTransport(lambda r: httpx.Response(code, text='PRIVATE ERROR')), **kw)):
                with self.assertRaises(clothing.ClothingError) as caught:
                    clothing.ask('typesafe', 'synthetic-test-key', {}, 'id')
                self.assertNotIn('PRIVATE', str(caught.exception))

    def test_cache_weather_settings_and_hourly_window_invalidation(self):
        with patch.object(clothing, 'ask', return_value=response()):
            self.generate()
        self.assertFalse(any(r['stale'] for r in clothing.public_status(self.now)['results']))
        with db.connect() as con:
            con.execute('UPDATE readings SET ts=ts-181')
        self.assertTrue(all(r['stale'] for r in clothing.public_status(self.now)['results']))
        self.seed()
        self.assertTrue(clothing.public_status(self.now+1801)['results'][0]['stale'])
        db.set_settings({'longitude': 1})
        self.assertTrue(all(r['stale'] for r in clothing.public_status(self.now)['results']))
        with patch.object(clothing, 'ask') as ask:
            with self.assertRaisesRegex(clothing.ClothingError, 'configured location'):
                self.generate()
            ask.assert_not_called()

    def test_identical_provider_input_reuses_cache_after_forecast_refresh(self):
        with patch.object(clothing, 'ask', return_value=response()) as ask:
            original = self.generate()
            with db.connect() as con:
                con.execute('UPDATE forecasts SET fetched=fetched+30,uv_index=9,cloud_cover=100')
            cfg = db.settings()
            marker = cfg['clothing_weather_source'] | {'fetched': cfg['clothing_weather_source']['fetched']+30}
            db.set_settings({'clothing_weather_source': marker})
            self.assertFalse(any(r['stale'] for r in clothing.public_status(self.now)['results']))
            self.assertEqual(self.generate()['usage'], original['usage'])
            self.assertEqual(ask.call_count, 3)
            with patch.dict(clothing.QUESTIONS, {'gloves': {'type': 'noul', 'instructions': 'Changed judgment'}}):
                self.assertTrue(all(r['stale'] for r in clothing.public_status(self.now)['results']))

    def test_partial_refresh_keeps_periods_chronological(self):
        # Forecast-only context isolates the advancing current-hour window.
        with db.connect() as con:
            con.execute('DELETE FROM readings')
        with patch.object(clothing, 'ask', return_value=response()) as ask:
            self.generate()
            self.now += 1801
            result = self.generate()
            self.assertEqual(ask.call_count, 4)
            self.assertEqual([r['period'] for r in result['results']], ['morning', 'afternoon', 'evening'])

    def test_timezone_setting_triggers_fresh_weather_collection(self):
        db.set_settings({'sensor_host': '192.0.2.1'})
        with patch.object(jobs, 'weather', new_callable=AsyncMock) as refresh:
            changed = self.client.post('/api/settings', json={'TZ': 'America/New_York'},
                                       headers={'X-Indigo-Request': '1'})
        self.assertEqual(changed.status_code, 200)
        refresh.assert_awaited_once()

    def test_initial_forecast_provenance_and_same_second_location_collision(self):
        cfg = db.settings()
        db.set_settings({'clothing_weather_source': {}})
        self.assertFalse(clothing.public_status(self.now)['weather_available'])
        db.set_settings({'longitude': 1})
        # An old-location request finishes after settings changed.
        jobs.store_forecasts([(self.now-60, self.now, 'weather', 50)], cfg)
        self.assertFalse(clothing.public_status(self.now)['weather_available'])
        # New location data must not inherit missing hours from old same-second rows.
        jobs.store_forecasts([(self.now-60, self.now, 'weather', 50)], db.settings())
        with db.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM forecasts WHERE kind='weather'").fetchone()[0], 1)
        self.assertFalse(clothing.public_status(self.now)['weather_available'])

    def test_settings_change_during_generation_stops_more_requests(self):
        def change(*args):
            db.set_settings({'longitude': 1})
            return response()
        with patch.object(clothing, 'ask', side_effect=change) as ask:
            with self.assertRaises(clothing.ClothingError):
                self.generate()
            self.assertEqual(ask.call_count, 1)
        status = clothing.public_status(self.now)
        self.assertEqual(status['usage']['input_tokens'], 100)
        self.assertTrue(status['results'][0]['stale'])
        self.assertIn('configured location', status['error'])
        self.assertEqual(status['unknown_usage_requests'], 0)

    def test_sensor_change_does_not_relabel_previous_reading(self):
        db.set_settings({'sensor_host': 'other.invalid'})
        states = clothing.weather_states(self.now, db.settings())
        self.assertIsNone(states[0]['state']['current_observation'])
        self.assertIn('forecasts only', states[0]['warning'])

    def test_settings_change_without_forecast_loss_stops_more_requests(self):
        def change(*args):
            db.set_settings({'sensor_host': 'other.invalid'})
            return response()
        with patch.object(clothing, 'ask', side_effect=change) as ask:
            with self.assertRaisesRegex(clothing.ClothingError, 'Weather or settings changed') as caught:
                self.generate()
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(ask.call_count, 1)
        status = clothing.public_status(self.now)
        self.assertEqual(status['usage']['input_tokens'], 100)
        self.assertEqual(status['unknown_usage_requests'], 0)

    def test_cumulative_usage_overflow_is_unknown_and_not_accumulated(self):
        limit = clothing.MAX_REPORTED_TOKENS
        first, second = response(), response()
        first['usage'] = {'input_tokens': limit, 'output_tokens': 20}
        second['usage'] = {'input_tokens': 1, 'output_tokens': 20}
        with patch.object(clothing, 'ask', side_effect=[first, second]) as ask:
            with self.assertRaisesRegex(clothing.ClothingError, 'accounting range') as caught:
                self.generate()
            self.assertEqual(caught.exception.status, 502)
            self.assertEqual(ask.call_count, 2)
        status = clothing.public_status(self.now)
        self.assertEqual(status['usage'], {'input_tokens': limit, 'output_tokens': 20})
        self.assertEqual(status['unknown_usage_requests'], 1)
        self.assertEqual(len(status['results']), 1)

    def test_usage_of_ignores_missing_or_malformed_usage(self):
        for value in (None, [], 'usage', {}, {'usage': None}, {'usage': []}, {'usage': 'x'}):
            self.assertIsNone(clothing.usage_of(value))
        self.assertIsNone(clothing.usage_of({'usage': {'input_tokens': -1, 'output_tokens': True}}))
        self.assertEqual(clothing.usage_of({'usage': {'input_tokens': 5, 'extra': 9}}), {'input_tokens': 5})

    def test_read_key_rejects_unknown_provider(self):
        for provider in ('other', '', None):
            with self.assertRaisesRegex(clothing.ClothingError, 'Choose jevmodel or typesafe'):
                clothing.read_key(provider)

    def test_configure_conflicts_with_running_generation(self):
        with clothing.LOCK:
            with self.assertRaisesRegex(clothing.ClothingError, 'already running') as caught:
                clothing.configure({'provider': 'typesafe'})
            self.assertEqual(caught.exception.status, 409)
        self.assertNotIn('clothing_provider', db.settings())

    def test_config_route_updates_provider_and_maps_errors(self):
        headers = {'X-Indigo-Request': '1'}
        saved = self.client.put('/api/clothing', json={'provider': 'typesafe'}, headers=headers)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['provider'], 'typesafe')
        self.assertEqual(db.settings()['clothing_provider'], 'typesafe')
        invalid = self.client.put('/api/clothing', json={'provider': 'other'}, headers=headers)
        self.assertEqual(invalid.status_code, 400)
        extra = self.client.put('/api/clothing', json={'provider': 'jevmodel', 'api_key': 'never-persist-this'},
                                headers=headers)
        self.assertEqual(extra.status_code, 400)
        self.assertNotIn('never-persist-this', json.dumps(db.settings()))
        self.assertEqual(db.settings()['clothing_provider'], 'typesafe')
        with clothing.LOCK:
            busy = self.client.put('/api/clothing', json={'provider': 'jevmodel'}, headers=headers)
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(db.settings()['clothing_provider'], 'typesafe')

    def test_adapter_rejects_oversized_and_unusable_provider_bodies(self):
        real_client = httpx.AsyncClient
        def ask_with(handler):
            with patch.object(clothing.httpx, 'AsyncClient', side_effect=lambda **kw: real_client(
                    transport=httpx.MockTransport(handler), **kw)):
                return clothing.ask('jevmodel', 'synthetic-test-key', {}, 'id')
        with self.assertRaisesRegex(clothing.ClothingError, 'size limit') as caught:
            ask_with(lambda r: httpx.Response(200, content=b'x'*65537))
        self.assertEqual(caught.exception.status, 502)
        def unreachable(request):
            raise httpx.ConnectError('PRIVATE NETWORK DETAIL', request=request)
        for handler in (lambda r: httpx.Response(200, content=b'not json PRIVATE'), unreachable):
            with self.assertRaisesRegex(clothing.ClothingError, 'billing may be unknown') as caught:
                ask_with(handler)
            self.assertEqual(caught.exception.status, 502)
            self.assertNotIn('PRIVATE', str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)
        def expire(awaitable, timeout):
            self.assertEqual(timeout, 30)
            awaitable.close()
            raise TimeoutError
        with patch.object(clothing.asyncio, 'wait_for', side_effect=expire):
            with self.assertRaisesRegex(clothing.ClothingError, 'billing may be unknown'):
                clothing.ask('jevmodel', 'synthetic-test-key', {}, 'id')
