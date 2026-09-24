import asyncio
import json
import logging
import os
from pathlib import Path
import time
import httpx
from . import db,backups
from .air import normalize, number

log=logging.getLogger('indigo')

def store_reading(data,cfg,now):
    row=normalize(data,cfg,now)
    with db.connect() as con:
        con.execute('INSERT OR IGNORE INTO readings ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
        con.execute('INSERT OR IGNORE INTO raw_samples VALUES (?,?)',(row['ts'],json.dumps(data)))

async def collect():
    cfg=await asyncio.to_thread(db.settings)
    source = cfg.get('sensor_source')
    if source not in ('local', 'purpleair_api'):
        if cfg.get('purpleair_sensor_index'):
            source = 'purpleair_api'
        elif cfg.get('sensor_host'):
            source = 'local'
        else:
            await asyncio.to_thread(db.status, 'sensor', 'Sensor is not configured')
            return

    if source == 'purpleair_api':
        sensor_index = cfg.get('purpleair_sensor_index')
        if not sensor_index:
            await asyncio.to_thread(db.status, 'sensor', 'PurpleAir sensor index is not configured')
            return
        api_key = cfg.get('purpleair_api_key') or os.environ.get('PURPLEAIR_API_KEY')
        if not api_key:
            key_file = os.environ.get('PURPLEAIR_API_KEY_FILE', '').strip()
            if key_file:
                try:
                    api_key = Path(key_file).read_text(encoding='utf-8').strip()
                except OSError:
                    pass
        if not api_key:
            await asyncio.to_thread(db.status, 'sensor', 'PurpleAir API key is not configured')
            return

        read_key = cfg.get('purpleair_read_key') or os.environ.get('PURPLEAIR_READ_KEY')
        if not read_key:
            read_file = os.environ.get('PURPLEAIR_READ_KEY_FILE', '').strip()
            if read_file:
                try:
                    read_key = Path(read_file).read_text(encoding='utf-8').strip()
                except OSError:
                    pass

        headers = {'X-API-Key': api_key, 'Connection': 'close', 'User-Agent': 'Indigo-Stats'}
        params = {}
        if read_key:
            params['read_key'] = read_key
        url = f'https://api.purpleair.com/v1/sensors/{sensor_index}'
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), trust_env=False, headers=headers) as client:
            for attempt in range(3):
                try:
                    response = await client.get(url, params=params)
                    if response.status_code in (401, 403):
                        await asyncio.to_thread(db.status, 'sensor', 'PurpleAir API key is invalid or unauthorized')
                        return
                    if response.status_code == 404:
                        await asyncio.to_thread(db.status, 'sensor', f'PurpleAir sensor {sensor_index} was not found')
                        return
                    response.raise_for_status()
                    await asyncio.to_thread(store_reading, response.json(), cfg, time.time())
                    break
                except (httpx.HTTPError, ValueError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))
        await asyncio.to_thread(db.status, 'sensor', success=True)
    else:
        host=cfg.get('sensor_host')
        if not host:
            await asyncio.to_thread(db.status,'sensor','Sensor is not configured')
            return
        # Plain HTTP stays on the local network. Never log host, response, or request URL.
        async with httpx.AsyncClient(timeout=httpx.Timeout(12,connect=4),trust_env=False,
                                     headers={'Connection':'close'}) as client:
            for attempt in range(3):
                try:
                    response=await client.get(f'http://{host}/json')
                    response.raise_for_status()
                    await asyncio.to_thread(store_reading,response.json(),cfg,time.time())
                    break
                except (httpx.HTTPError, ValueError):
                    if attempt == 2: raise
                    await asyncio.sleep(2*(attempt+1))
        await asyncio.to_thread(db.status,'sensor',success=True)

def _series_val(series, key, i, low=0, high=10000):
    vals = series.get(key)
    return number(vals[i], low, high) if vals is not None and i < len(vals) else None

async def weather():
    cfg=await asyncio.to_thread(db.settings)
    if not cfg.get('forecast_enabled',False):
        await asyncio.to_thread(db.status,'weather','Forecasts are disabled in container settings')
        return
    if 'latitude' not in cfg or 'longitude' not in cfg:
        await asyncio.to_thread(db.status,'weather','Forecast location is not configured')
        return
    common=dict(latitude=cfg['latitude'],longitude=cfg['longitude'],timezone=cfg.get('timezone','auto'),timeformat='unixtime')
    now=int(time.time())
    async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
        for kind,url,params in [
            ('weather','https://api.open-meteo.com/v1/forecast',dict(
                hourly='temperature_2m,relative_humidity_2m,uv_index,precipitation_probability,weather_code,wind_speed_10m,apparent_temperature,cloud_cover',
                daily='sunrise,sunset,weather_code,temperature_2m_max,temperature_2m_min',
                temperature_unit='fahrenheit',
                wind_speed_unit='mph',
                forecast_days=10)),
            ('air','https://air-quality-api.open-meteo.com/v1/air-quality',dict(hourly='pm2_5,us_aqi',forecast_days=7))]:
            response=await client.get(url,params=common|params)
            response.raise_for_status()
            data=response.json()
            h=data['hourly']
            rows=[]
            i=0
            val=lambda key,low=0,high=10000: _series_val(h,key,i,low,high)
            for i,t in enumerate(h['time']):
                w_code = int(w) if (w := val('weather_code',0,99)) is not None else None
                rows.append((now,t,kind,
                    val('temperature_2m',-100,200),
                    val('relative_humidity_2m',0,100),
                    val('pm2_5'),
                    val('uv_index',0,50),
                    val('precipitation_probability',0,100),
                    val('us_aqi',0,500),
                    w_code if kind=='weather' else None,
                    val('wind_speed_10m',0,300) if kind=='weather' else None,
                    val('apparent_temperature',-100,200) if kind=='weather' else None,
                    val('cloud_cover',0,100) if kind=='weather' else None,
                    None, None, None))
            if kind=='weather' and 'daily' in data:
                d=data['daily']
                i=0
                dval=lambda key,low=0,high=10000: _series_val(d,key,i,low,high)
                for i,t in enumerate(d['time']):
                    dw_code = int(w) if (w := dval('weather_code',0,99)) is not None else None
                    sunrise = int(s) if (s := dval('sunrise',0,2500000000)) is not None else None
                    sunset = int(s) if (s := dval('sunset',0,2500000000)) is not None else None
                    rows.append((now,t,'daily',
                        dval('temperature_2m_max',-100,200),
                        None, None, None, None, None,
                        dw_code,
                        None, None, None,
                        sunrise, sunset,
                        dval('temperature_2m_min',-100,200)))
            await asyncio.to_thread(store_forecasts,rows)
    await asyncio.to_thread(db.status,'weather',success=True)

def store_forecasts(rows):
    padded = [r + (None,)*(16-len(r)) if len(r) < 16 else r for r in rows]
    with db.connect() as con:
        con.executemany('INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',padded)

def maintenance():
    now=int(time.time())
    with db.connect() as con:
        con.execute('DELETE FROM raw_samples WHERE ts<?',(now-30*86400,))
        # Preserve one pre-target snapshot per kind/hour for past forecast evaluation.
        con.execute('''DELETE FROM forecasts WHERE valid<? AND fetched <
            (SELECT MAX(f.fetched) FROM forecasts f WHERE f.kind=forecasts.kind
             AND f.valid=forecasts.valid AND f.fetched<=f.valid)''',(now-86400,))
        con.execute('DELETE FROM forecasts WHERE valid<? AND fetched>valid',(now-86400,))
    with db.connect() as con:
        last=con.execute("SELECT last_success FROM job_status WHERE name='backup'").fetchone()
    if not last or not last[0] or now-last[0]>=86400:
        db.backup()
        db.status('backup',success=True)
    db.status('maintenance',success=True)

async def offsite_backup():
    # A failed remote transfer never changes the result of the local snapshot job.
    try:
        await asyncio.to_thread(backups.run, wait=True)
    except Exception as exc:
        # The backup service has already stored its actionable, sanitized error.
        log.warning('offsite_backup failed (%s)',type(exc).__name__)

async def loop(name,task,interval):
    while True:
        try:
            if asyncio.iscoroutinefunction(task): await task()
            else: await asyncio.to_thread(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Exception text often contains URLs/location; never expose it.
            await asyncio.to_thread(db.status,name,f'{type(exc).__name__}: job failed; will retry')
            log.warning('%s failed (%s)',name,type(exc).__name__)
        delay=interval-time.time()%interval
        await asyncio.sleep(max(1,delay))
