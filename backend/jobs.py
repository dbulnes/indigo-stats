import asyncio
import json
import logging
import time
import httpx
from . import db
from .air import normalize, number

log=logging.getLogger('indigo')

def store_reading(data,cfg,now):
    row=normalize(data,cfg,now)
    with db.connect() as con:
        con.execute('INSERT OR IGNORE INTO readings ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
        con.execute('INSERT OR IGNORE INTO raw_samples VALUES (?,?)',(row['ts'],json.dumps(data)))

async def collect():
    cfg=await asyncio.to_thread(db.settings)
    host=cfg.get('sensor_host')
    if not host:
        db.status('sensor','Sensor is not configured')
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

async def weather():
    cfg=await asyncio.to_thread(db.settings)
    if not cfg.get('forecast_enabled',False):
        db.status('weather','Forecasts are disabled in container settings')
        return
    if 'latitude' not in cfg or 'longitude' not in cfg:
        db.status('weather','Forecast location is not configured')
        return
    common=dict(latitude=cfg['latitude'],longitude=cfg['longitude'],timezone='GMT',timeformat='unixtime',forecast_days=5)
    now=int(time.time())
    async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
        for kind,url,params in [
            ('weather','https://api.open-meteo.com/v1/forecast',dict(hourly='temperature_2m,relative_humidity_2m,uv_index,precipitation_probability',temperature_unit='fahrenheit')),
            ('air','https://air-quality-api.open-meteo.com/v1/air-quality',dict(hourly='pm2_5,us_aqi'))]:
            response=await client.get(url,params=common|params)
            response.raise_for_status()
            h=response.json()['hourly']
            rows=[]
            for i,t in enumerate(h['time']):
                val=lambda key,low=0,high=10000: number(h.get(key,[None]*len(h['time']))[i],low,high)
                rows.append((now,t,kind,val('temperature_2m',-100,200),val('relative_humidity_2m',0,100),val('pm2_5'),val('uv_index',0,50),val('precipitation_probability',0,100),val('us_aqi',0,500)))
            await asyncio.to_thread(store_forecasts,rows)
    await asyncio.to_thread(db.status,'weather',success=True)

def store_forecasts(rows):
    with db.connect() as con:
        con.executemany('INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?,?,?,?,?,?)',rows)

def maintenance():
    now=int(time.time())
    with db.connect() as con:
        for span in (3600,86400):
            con.execute('''INSERT OR REPLACE INTO summaries
                SELECT ts/?*?, ?, COUNT(*),AVG(temperature),AVG(humidity),AVG(pm25),
                MIN(temperature),MAX(temperature),MIN(pm25),MAX(pm25)
                FROM readings WHERE ts>=? GROUP BY ts/?''',(span,span,span,now-3*86400,span))
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

async def loop(name,task,interval):
    while True:
        try:
            if name=='maintenance': await asyncio.to_thread(task)
            else: await task()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Exception text often contains URLs/location; never expose it.
            await asyncio.to_thread(db.status,name,f'{type(exc).__name__}: job failed; will retry')
            log.warning('%s failed (%s)',name,type(exc).__name__)
        delay=interval-time.time()%interval
        await asyncio.sleep(max(1,delay))
