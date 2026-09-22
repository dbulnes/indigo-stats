import asyncio
import csv
import io
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from . import db,jobs,config,backups
from .air import aqi,nowcast,environment_values,environment_sql
from typing import Literal

APP_VERSION = '0.6.2'
SCHEDULES = (
    ('sensor', jobs.collect, 60),
    ('weather', jobs.weather, 3600),
    ('maintenance', jobs.maintenance, 3600),
    ('offsite_backup', jobs.offsite_backup, 3600),
)
@asynccontextmanager
async def lifespan(app):
    db.initialize()
    config.import_environment()
    tasks=[]
    if os.getenv('DISABLE_JOBS')!='1':
        tasks=[asyncio.create_task(jobs.loop(name,task,interval)) for name,task,interval in SCHEDULES]
    yield
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
    with db.connect() as con: con.execute('PRAGMA wal_checkpoint(TRUNCATE)')

app=FastAPI(title='Indigo Stats',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

@app.middleware('http')
async def headers(request,call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' https://geocode.arcgis.com https://api.open-meteo.com; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    if request.url.path.startswith('/api/') or request.url.path in ('/sw.js','/index.html','/'):
        response.headers['Cache-Control']='no-store'
    return response

@app.get('/api/health')
def health():
    with db.connect() as con: con.execute('SELECT 1').fetchone()
    return {'ok':True,'version':APP_VERSION}

@app.get('/api/status')
def status():
    with db.connect() as con:
        jobs_status=[dict(r) for r in con.execute('SELECT * FROM job_status ORDER BY name')]
        counts=con.execute('SELECT COUNT(*) n,MIN(ts) first,MAX(ts) last FROM readings').fetchone()
        size=con.execute('PRAGMA page_count').fetchone()[0]*con.execute('PRAGMA page_size').fetchone()[0]
    backup = backups.public_status()
    scope = ('Local snapshots plus verified ' + backup['provider'].replace('_', ' ') + ' copies'
             if backup['enabled'] else 'Local snapshots only; off-server backup is disabled')
    return dict(jobs=jobs_status,readings=dict(counts),database_bytes=size,timezone=db.settings().get('timezone','Etc/UTC'),
                backup_scope=scope,version=APP_VERSION)

@app.get('/api/backups')
def backup_status():
    return backups.public_status()

@app.put('/api/backups')
def configure_backups(data: dict):
    try: backups.set_config(data)
    except ValueError as exc: raise HTTPException(400, str(exc)) from None
    return backups.public_status()

@app.post('/api/backups/test')
def test_backup_destination():
    try: backups.probe()
    except backups.BackupError as exc: raise HTTPException(400, str(exc)) from None
    except Exception: raise HTTPException(502, 'Destination probe failed') from None
    return {'ok': True}

@app.post('/api/backups/run', status_code=202)
def run_backup_now():
    if not backups.start_async(): raise HTTPException(409, 'An off-server backup is already running')
    return {'accepted': True}

@app.get('/api/backups/google/connect')
def google_connect():
    try: return RedirectResponse(backups.google_authorization_url(), status_code=302)
    except backups.BackupError as exc: raise HTTPException(400, str(exc)) from None

@app.get('/api/backups/google/callback')
def google_callback(code: str = Query(min_length=1), state: str = Query(min_length=1)):
    try: backups.google_callback(code, state)
    except backups.BackupError as exc: raise HTTPException(400, str(exc)) from None
    except Exception: raise HTTPException(502, 'Google authorization failed') from None
    return HTMLResponse('''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Indigo · Google Drive Linked</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; background: #101723; color: #e2e8f0; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 1rem; box-sizing: border-box; }
    .card { background: #1a2333; border: 1px solid #2d3748; border-radius: 12px; padding: 2rem; max-width: 420px; width: 100%; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
    h1 { font-size: 1.25rem; margin: 0 0 0.75rem; color: #f8fafc; }
    p { margin: 0 0 1.25rem; color: #94a3b8; font-size: 0.95rem; line-height: 1.5; }
    a.btn { display: inline-block; background: #3b82f6; color: #fff; padding: 0.6rem 1.2rem; border-radius: 6px; text-decoration: none; font-weight: 500; font-size: 0.9rem; }
    a.btn:hover { background: #2563eb; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Google Drive linked</h1>
    <p id="msg">Google Drive authorization was successful. This window will close automatically.</p>
    <a href="/" class="btn" id="btn" style="display:none">Return to Indigo Stats</a>
  </div>
  <script>
    try {
      if (window.opener) {
        window.opener.postMessage({ type: 'indigo-google-auth-success' }, window.location.origin);
        setTimeout(function() { window.close(); }, 1200);
      } else {
        document.getElementById('msg').textContent = 'Google Drive authorization was successful. You may return to the app.';
        document.getElementById('btn').style.display = 'inline-block';
      }
    } catch (e) {
      document.getElementById('btn').style.display = 'inline-block';
    }
  </script>
</body>
</html>''')

@app.post('/api/backups/google/unlink')
def google_unlink():
    backups.google_unlink()
    return {'ok': True}


@app.get('/api/settings')
def get_settings():
    s = db.settings()
    return {
        'FORECAST_ENABLED': s.get('forecast_enabled'),
        'PM_METHOD': s.get('pm_method'),
        'ENVIRONMENT_MODE': s.get('environment_mode'),
        'SENSOR_PLACEMENT': s.get('placement'),
        'TZ': s.get('timezone'),
        'HAS_FORECAST_LOCATION': 'latitude' in s and 'longitude' in s,
        'UNITS': s.get('units', 'imperial'),
    }

@app.post('/api/settings')
def post_settings(data: dict, background_tasks: BackgroundTasks):
    update = {}
    allowed = ['FORECAST_ENABLED', 'PM_METHOD', 'ENVIRONMENT_MODE', 'SENSOR_PLACEMENT', 'TZ', 'FORECAST_LATITUDE', 'FORECAST_LONGITUDE', 'SENSOR_HOST', 'UNITS']
    for env in allowed:
        if env in data and data[env] is not None and str(data[env]).strip() != '':
            key, cast = config.ENV_FIELDS[env]
            try:
                update[key] = cast(str(data[env]))
            except ValueError:
                raise HTTPException(400, f'Invalid value for {env}')
    merged = db.settings() | update
    try:
        config.validate(merged)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.set_settings(update)
    if (merged.get('forecast_enabled') and 'latitude' in merged and 'longitude' in merged) and (
        'forecast_enabled' in update or 'latitude' in update or 'longitude' in update
    ):
        background_tasks.add_task(jobs.weather)
    return {'ok': True}

@app.get('/api/latest')
def latest(environment:Literal['purpleair','raw','simple']|None=None):
    mode=environment or db.settings().get('environment_mode','purpleair')
    now=int(time.time())
    with db.connect() as con:
        row=con.execute('SELECT * FROM readings ORDER BY ts DESC LIMIT 1').fetchone()
        current_hour=now//3600*3600
        hourly={r['hour']:r['pm'] for r in con.execute('''SELECT ts/3600*3600 hour,AVG(pm25) pm
            FROM readings WHERE ts>=? AND ts<? AND pm25 IS NOT NULL
            GROUP BY hour HAVING COUNT(*)>=45''',(current_hour-12*3600,current_hour))}
    pm=nowcast([hourly.get(current_hour-3600*(i+1)) for i in range(12)])
    value=dict(row) if row else None
    if value:
        value['temperature'],value['humidity']=environment_values(value['temperature_raw'],value['humidity_raw'],mode)
        value['environment_mode']=mode
        value['aqi']=aqi(value['pm25'])
        value['beyond_scale']=value['pm25'] is not None and value['pm25']>325.4
    return dict(reading=value,nowcast_aqi=aqi(pm),nowcast_pm25=pm,nowcast_beyond_scale=pm is not None and pm>325.4,
                stale=not row or now-row['ts']>180,server_time=now)

def bounds(start,end):
    if end<=start or end-start>3660*86400:
        raise HTTPException(400,'Choose a range of up to ten years with end after start')

def forecast_rows(con,start,end,historical=True):
    # Only forecasts known before the hour began count as historical predictions.
    return [dict(r) for r in con.execute('''SELECT f.* FROM forecasts f WHERE kind IN ('weather','air') AND valid>=? AND valid<?
        AND fetched=(SELECT MAX(x.fetched) FROM forecasts x WHERE x.kind=f.kind AND x.valid=f.valid
        AND (?=0 OR x.fetched<=x.valid)) ORDER BY valid''',(start,end,1 if historical else 0))]

@app.get('/api/history')
def history(start:int=Query(ge=0),end:int=Query(ge=0),step:int=Query(default=60,ge=60),threshold:float|None=None,environment:Literal['purpleair','raw','simple']|None=None):
    mode=environment or db.settings().get('environment_mode','purpleair')
    temp,rh=environment_sql(mode)
    bounds(start,end)
    if step not in (60,300,900,3600,21600,86400): raise HTTPException(400,'Invalid resolution')
    # Bound graph payloads independent of requested duration.
    effective=next((s for s in (60,300,900,3600,21600,86400) if s>=step and (end-start)/s<=1600),86400)
    with db.connect() as con:
        rows=[dict(r) for r in con.execute(f'''SELECT ts/?*? ts,AVG({temp}) temperature,AVG({rh}) humidity,
            AVG(pm25) pm25,MIN(pm25) pm_min,MAX(pm25) pm_max,COUNT(*) samples,
            SUM(CASE WHEN quality!='' THEN 1 ELSE 0 END) flagged
            FROM readings WHERE ts>=? AND ts<? GROUP BY ts/?''',(effective,effective,start,end,effective))]
        stats=dict(con.execute(f'''SELECT COUNT(*) samples,MIN({temp}) temp_min,MAX({temp}) temp_max,
            AVG({temp}) temp_mean,AVG({rh}) humidity_mean,AVG(pm25) pm_mean,
            MAX(pm25) pm_max,MIN(pm25) pm_min FROM readings WHERE ts>=? AND ts<?''',(start,end)).fetchone())
        forecasts=forecast_rows(con,start,end)
    for r in rows:
        r['aqi']=aqi(r['pm25'])
        r['above_threshold']=threshold is not None and r['pm_max'] is not None and r['pm_max']>=threshold
    return dict(points=rows,forecasts=forecasts,step=effective,stats=stats,environment_mode=mode)

@app.get('/api/forecast')
def forecast():
    now=int(time.time())//3600*3600
    with db.connect() as con:
        hourly=forecast_rows(con,now,now+3*86400,False)
        daily=[dict(r) for r in con.execute(
            '''SELECT * FROM forecasts WHERE kind='daily' AND valid >= ? AND valid < ?
            AND fetched = (SELECT MAX(f.fetched) FROM forecasts f
            WHERE f.kind='daily' AND f.valid=forecasts.valid)
            ORDER BY valid''', (now-86400, now+11*86400))]
    for row in hourly:
        row['aqi']=aqi(row['pm25'])
    return {'points':hourly, 'daily':daily}

@app.get('/api/export')
def export(start:int=Query(ge=0),end:int=Query(ge=0)):
    bounds(start,end)
    def chunks():
        fields=['ts','source_ts','temperature','humidity','temperature_raw','humidity_raw','pm_a','pm_b','pm25','method','quality','environment_mode']
        # Keyset pages keep long exports from holding a WAL read transaction open.
        out=io.StringIO(); writer=csv.writer(out); writer.writerow(fields)
        yield out.getvalue(); cursor=start-1
        while True:
            with db.connect() as con:
                rows=con.execute('SELECT '+','.join(fields)+' FROM readings WHERE ts>? AND ts<? ORDER BY ts LIMIT 2000',(cursor,end)).fetchall()
            if not rows: break
            out=io.StringIO(); writer=csv.writer(out)
            for row in rows:
                # Prevent spreadsheet formula interpretation of untrusted sensor text.
                writer.writerow([("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v) for v in row])
            yield out.getvalue(); cursor=rows[-1]['ts']
    return StreamingResponse(chunks(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="indigo-readings.csv"'})

WEB=Path(os.getenv('WEB_DIR',str(Path(__file__).parent.parent/'web'/'dist')))
if (WEB/'assets').exists(): app.mount('/assets',StaticFiles(directory=WEB/'assets'),name='assets')
@app.get('/{path:path}')
def frontend(path:str):
    if not WEB.exists(): raise HTTPException(404)
    if path.startswith('api/') or path=='api': raise HTTPException(404)
    resolved=(WEB/path).resolve()
    if not resolved.is_relative_to(WEB.resolve()): raise HTTPException(404)
    if resolved.is_file(): return FileResponse(resolved)
    if '.' in path: raise HTTPException(404)
    if not (WEB/'index.html').exists(): raise HTTPException(404)
    return FileResponse(WEB/'index.html')
