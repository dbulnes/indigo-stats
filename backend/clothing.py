"""Manual, bounded clothing judgments over privacy-minimized weather summaries."""
import asyncio
import hashlib
import json
import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from . import db, jobs
from .air import environment_values

PROVIDERS = {
    'jevmodel': 'https://jevmodel.org/v1/systemone',
    'typesafe': 'https://api.typesafe.ai/v1/systemone',
}
PERIODS = (('morning', 6, 11), ('afternoon', 11, 17), ('evening', 17, 23))
CHOICES = {
    'bottoms': ['Shorts', 'Lightweight trousers', 'Warm trousers', 'Insulated trousers'],
    'base_tops': ['Sleeveless top', 'Short-sleeved top', 'Long-sleeved top', 'Thermal base top'],
    'midlayers': ['No midlayer', 'Light sweater', 'Fleece', 'Heavy insulating midlayer'],
    'outerwear': ['No outerwear', 'Wind shell', 'Rain shell', 'Insulated jacket', 'Insulated waterproof coat'],
    'footwear': ['Open footwear', 'Breathable closed shoes', 'Waterproof shoes', 'Insulated waterproof boots'],
}
ACCESSORIES = ('umbrella', 'beanie', 'gloves')
ASSUMPTIONS = {
    'activity': 'Ordinary light outdoor activity and average temperature sensitivity; functional comfort, not style.',
    'base_tops': 'Assume a removable midlayer and protective outerwear can be added as needed.',
    'midlayers': 'Assume a comfortable base top and an uninsulated wind/rain shell if needed.',
    'outerwear': 'Assume a comfortable base top and an ordinary light midlayer in cool weather, not a heavy insulating midlayer.',
    'other': 'Bottoms, footwear and accessories are judged independently with otherwise weather-appropriate clothing.',
}
QUESTIONS = {
    group: {
        'type': 'choice',
        'instructions': f'Which {group.replace("_", " ")} option best suits `forecast`? '
                        'Use the explicit layer and activity assumptions in `assumptions`. '
                        'Select functional comfort, not style. Questions are independent: do not assume '
                        'another question answer. `current_observation` is current local context only, '
                        'not a prediction for later hours.',
        'criteria': {str(i): label for i, label in enumerate(options)},
    } for group, options in CHOICES.items()
} | {
    item: {
        'type': 'noul',
        'instructions': f'Would a {item} be useful during `forecast`, following `assumptions`? '
                        'Judge independently of other accessories. Current observations are not forecasts.',
    } for item in ACCESSORIES
}
LOCK = threading.Lock()
CACHE_SECONDS = 3600
COOLDOWN_SECONDS = 300
MAX_REPORTED_TOKENS = 2**53 - 1  # Exact integers in the browser's JSON number representation.


class ClothingError(Exception):
    def __init__(self, message, status=400, unbilled=False):
        super().__init__(message)
        self.status = status
        self.unbilled = unbilled


def read_key(provider):
    """Server environment only: never import credentials into SQLite or backups."""
    if provider not in PROVIDERS:
        raise ClothingError('Choose jevmodel or typesafe.')
    variable = {'jevmodel': 'CLOTHING_JEVMODEL_API_KEY', 'typesafe': 'CLOTHING_TYPESAFE_API_KEY'}[provider]
    key = os.environ.get(variable, '').strip()
    if not key or len(key) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ClothingError('Configure a valid clothing API key for the selected provider in the container environment.')
    return key


def requested_provider(data):
    if set(data) != {'provider'} or not isinstance(data['provider'], str) or data['provider'] not in PROVIDERS:
        raise ClothingError('Choose jevmodel or typesafe.')
    return data['provider']


def configure(data):
    provider = requested_provider(data)
    if not LOCK.acquire(blocking=False):
        raise ClothingError('Generation is already running.', 409)
    try:
        db.set_settings({'clothing_provider': provider})
    finally:
        LOCK.release()


def finite(value, low, high):
    return type(value) in (int, float) and low <= value <= high and math.isfinite(value)


def observation_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('/', '-').replace('z', '+00:00').replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except (ValueError, OSError, OverflowError):
        return None


def forecast_context(settings):
    return jobs.forecast_context(settings)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def weather_states(now, settings):
    if not settings.get('forecast_enabled'):
        raise ClothingError('Enable forecasts before generating clothing recommendations.')
    zone = ZoneInfo(settings.get('timezone', 'Etc/UTC'))
    local = datetime.fromtimestamp(now, zone)
    if local.hour >= 23:
        return []
    states = []
    observation = None
    warning = 'Fresh outdoor PurpleAir temperature and humidity unavailable; using forecasts only.'
    with db.connect() as con:
        # Provenance and rows must be one snapshot, including same-second replacement.
        con.execute('BEGIN')
        marker = con.execute("SELECT value FROM settings WHERE key='clothing_weather_source'").fetchone()
        provenance = json.loads(marker['value']) if marker else {}
        if provenance.get('context') != forecast_context(settings):
            raise ClothingError('Wait for fresh Open-Meteo forecasts for the configured location and timezone.')
        reading = con.execute('SELECT * FROM readings ORDER BY ts DESC LIMIT 1').fetchone()
        sensor_marker = con.execute("SELECT value FROM settings WHERE key='clothing_sensor_source'").fetchone()
        sensor = json.loads(sensor_marker['value']) if sensor_marker else {}
        source_ts = observation_time(reading['source_ts']) if reading else None
        if (reading and 0 <= now - reading['ts'] <= 180
                and sensor.get('ts') == reading['ts'] and sensor.get('context') == jobs.sensor_context(settings)
                and source_ts is not None and 0 <= now - source_ts <= 600
                and settings.get('placement', 'outdoors') == 'outdoors'):
            temp, humidity = environment_values(reading['temperature_raw'], reading['humidity_raw'],
                                                settings.get('environment_mode', 'purpleair'))
            if finite(temp, -100, 200) and finite(humidity, 0, 100):
                observation = {'source': 'PurpleAir current observation', 'age_seconds': now - source_ts,
                               'temperature_f': round(temp, 1), 'humidity_percent': round(humidity, 1)}
                warning = None
        for name, start_hour, end_hour in PERIODS:
            start = int(local.replace(hour=start_hour, minute=0, second=0, microsecond=0).timestamp())
            end = int(local.replace(hour=end_hour, minute=0, second=0, microsecond=0).timestamp())
            if end <= now:
                continue
            first = start + max(0, (now - start) // 3600) * 3600
            rows = [dict(r) for r in con.execute(
                """SELECT * FROM forecasts WHERE kind='weather' AND valid>=? AND valid<?
                AND fetched=? ORDER BY valid""", (first, end, provenance.get('fetched')))]
            fields = {'temperature': (-100, 200), 'apparent_temperature': (-100, 200),
                      'humidity': (0, 100), 'precipitation_probability': (0, 100), 'wind_speed': (0, 300)}
            if (len(rows) != (end - first) // 3600
                    or any(r['valid'] != first + i * 3600 or not 0 <= now-r['fetched'] <= 10800
                           or any(not finite(r[k], *bounds) for k, bounds in fields.items())
                           for i, r in enumerate(rows))):
                raise ClothingError(f'Fresh, complete Open-Meteo forecasts are required for {name}.')
            summary = {'source': 'Open-Meteo forecast', 'period': name,
                       'local_hours': f'{max(start_hour, local.hour):02d}:00–{end_hour:02d}:00',
                       'temperature_f': [min(r['temperature'] for r in rows), max(r['temperature'] for r in rows)],
                       'apparent_temperature_f': [min(r['apparent_temperature'] for r in rows),
                                                  max(r['apparent_temperature'] for r in rows)],
                       'humidity_percent': [min(r['humidity'] for r in rows), max(r['humidity'] for r in rows)],
                       'precipitation_probability_max_percent': max(r['precipitation_probability'] for r in rows),
                       'wind_speed_max_mph': max(r['wind_speed'] for r in rows)}
            state = {'forecast': summary, 'current_observation': observation, 'assumptions': ASSUMPTIONS}
            # Age changes alone should not spend again, but lost/fresh evidence and hourly
            # in-progress windows must invalidate previous judgments.
            # Cache the actual judgment input, not unused weather fields or fetch metadata.
            evidence = {'state': state | {'current_observation': None if observation is None else {
                k: v for k, v in observation.items() if k != 'age_seconds'}}, 'questions': QUESTIONS}
            states.append({'period': name, 'start': start, 'end': end, 'window_start': first,
                           'forecast_fetched': provenance['fetched'], 'evidence': fingerprint(evidence),
                           'state': state, 'warning': warning})
    return states


def usage_of(response):
    usage = response.get('usage') if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None
    return {k: usage[k] for k in ('input_tokens', 'output_tokens')
            if type(usage.get(k)) is int and 0 <= usage[k] <= MAX_REPORTED_TOKENS} or None


def validate_response(response):
    """Whitelist outputs; never retain arbitrary provider text."""
    try:
        answers = response['answers']
        if set(usage_of(response) or {}) != {'input_tokens', 'output_tokens'} or set(answers) != set(QUESTIONS):
            raise ValueError()
        result = {}
        for group, labels in CHOICES.items():
            answer = answers[group]
            probabilities = answer['probabilities']
            if (answer['type'] != 'choice' or set(probabilities) != set(QUESTIONS[group]['criteria'])
                    or not all(finite(p, 0, 1) for p in probabilities.values())
                    or abs(sum(probabilities.values()) - 1) > 0.001
                    or not finite(answer['confidence'], 0, 1)
                    or answer['choice'] not in probabilities
                    or probabilities[answer['choice']] != max(probabilities.values())):
                raise ValueError()
            result[group] = [{'label': labels[int(key)], 'probability': probability}
                             for key, probability in sorted(probabilities.items(), key=lambda item: (-item[1], item[0]))]
        for item in ACCESSORIES:
            if answers[item]['type'] != 'noul' or not finite(answers[item]['noul'], 0, 1):
                raise ValueError()
            result[item] = answers[item]['noul']
        return result
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ClothingError('Provider returned an invalid response. No automatic retry was made.', 502) from None


def ask(provider, key, state, request_id):
    """Fixed endpoints, documented System One shape, bounded I/O, no automatic retries."""
    async def request():
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=5), follow_redirects=False,
                                     trust_env=False) as client:
            async with client.stream('POST', PROVIDERS[provider],
                               headers={'Authorization': f'Bearer {key}', 'Idempotency-Key': request_id},
                               json={'model': 'jev-latest', 'state': state, 'questions': QUESTIONS}) as response:
                if response.status_code != 200:
                    messages = {401: 'Provider rejected the API key.', 403: 'Provider denied access.',
                                402: 'Provider credits exhausted. Add credits before trying again.',
                                429: 'Provider rate limit reached. Try again later.'}
                    unbilled = provider == 'jevmodel' and response.status_code in (401, 402, 422, 429, 502)
                    raise ClothingError(messages.get(response.status_code, 'Provider request failed. Try again later.'),
                                        502, unbilled=unbilled)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise ClothingError('Provider response exceeded the size limit.', 502)
                return json.loads(body)

    async def bounded():
        return await asyncio.wait_for(request(), timeout=30)

    try:
        return asyncio.run(bounded())
    except (httpx.HTTPError, ValueError, TimeoutError, RecursionError):
        raise ClothingError('Provider response unavailable or invalid; billing may be unknown. No automatic retry was made.',
                            502) from None


def context_key(settings):
    fields = ('latitude', 'longitude', 'timezone', 'environment_mode', 'placement',
              'forecast_enabled', 'sensor_source', 'sensor_host', 'purpleair_sensor_index')
    return fingerprint({k: settings.get(k) for k in fields})


def valid_cache(result, period, now, provider):
    return (result['start'] == period['start'] and result['provider'] == provider
            and 0 <= now-result['generated'] < CACHE_SECONDS
            and result.get('window_start') == period['window_start']
            and result.get('evidence') == period['evidence'])


def public_status(now=None):
    now = int(time.time()) if now is None else now
    settings = db.settings()
    saved = settings.get('clothing_state', {})
    provider = settings.get('clothing_provider', 'jevmodel')
    try:
        read_key(provider)
        configured = True
    except ClothingError:
        configured = False
    try:
        states = weather_states(now, settings)
        weather_error = None
    except ClothingError as error:
        states = []
        weather_error = str(error)
    results = [{k: v for k, v in r.items() if k not in ('evidence', 'window_start')} | {
                   'stale': saved.get('context') != context_key(settings)
                   or not any(valid_cache(r, s, now, provider) for s in states)}
               for r in sorted(saved.get('results', []), key=lambda item: item['start']) if r['end'] > now]
    return {'provider': provider, 'configured': configured, 'busy': LOCK.locked(),
            'results': results, 'usage': saved.get('usage', {'input_tokens': 0, 'output_tokens': 0}),
            'unknown_usage_requests': saved.get('unknown_usage_requests', 0),
            'weather_available': bool(states), 'weather_error': weather_error,
            'error': saved.get('error'), 'retry_after': max(0, saved.get('last_attempt', 0)+COOLDOWN_SECONDS-now)}


def generate(data):
    expected_provider = requested_provider(data)
    if not LOCK.acquire(blocking=False):
        raise ClothingError('Generation is already running; no additional requests were sent.', 409)
    try:
        now = int(time.time())
        settings = db.settings()
        provider = settings.get('clothing_provider', 'jevmodel')
        if provider != expected_provider:
            raise ClothingError('The selected provider changed. Refresh and review it before generating; no requests were sent.', 409)
        key = read_key(provider)
        states = weather_states(now, settings)
        saved = settings.get('clothing_state', {})
        context = context_key(settings)
        results = [r for r in saved.get('results', []) if r['end'] > now] if saved.get('context') == context else []
        pending = [s for s in states if not any(valid_cache(r, s, now, provider) for r in results)]
        if pending and now-saved.get('last_attempt', 0) < COOLDOWN_SECONDS:
            raise ClothingError('Wait five minutes between generation attempts; no requests were sent.', 429)
        if pending:
            saved.update(results=results, context=context, error=None, last_attempt=now)
            saved.setdefault('usage', {'input_tokens': 0, 'output_tokens': 0})
            saved.setdefault('unknown_usage_requests', 0)
        for period in pending:
            try:
                current = db.settings()
                current_states = weather_states(int(time.time()), current)
                if context_key(current) != context or not any(
                        s['start'] == period['start'] and s['evidence'] == period['evidence'] for s in current_states):
                    raise ClothingError('Weather or settings changed during generation. Refresh before trying again.', 409)
                # Persist uncertainty before network I/O, including process interruptions.
                saved['unknown_usage_requests'] += 1
                db.set_settings({'clothing_state': saved})
                response = ask(provider, key, period['state'], str(uuid.uuid4()))
                usage = usage_of(response)
                if usage:
                    if any(saved['usage'][name] + count > MAX_REPORTED_TOKENS for name, count in usage.items()):
                        raise ClothingError('Provider usage exceeds the supported accounting range; usage is unknown.', 502)
                    for name, count in usage.items():
                        saved['usage'][name] += count
                    if len(usage) == 2:
                        saved['unknown_usage_requests'] -= 1
                    db.set_settings({'clothing_state': saved})
                judgments = validate_response(response)
                result = period | {'generated': int(time.time()), 'provider': provider,
                                   'judgments': judgments, 'usage': usage}
                saved['results'] = [r for r in saved['results'] if r['start'] != period['start']] + [result]
                db.set_settings({'clothing_state': saved})
            except ClothingError as error:
                if error.unbilled:
                    saved['unknown_usage_requests'] -= 1
                saved['error'] = str(error)
                db.set_settings({'clothing_state': saved})
                raise
    finally:
        LOCK.release()
    return public_status()
