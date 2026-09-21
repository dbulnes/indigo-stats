import asyncio
import csv
import io
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from . import db,jobs,config
from .air import aqi,nowcast,environment_values,environment_sql
from typing import Literal

APP_VERSION = '0.1.2'

@asynccontextmanager
async def lifespan(app):
    db.initialize()
    config.import_environment()
    tasks=[]
    if os.getenv('DISABLE_JOBS')!='1':
        tasks=[asyncio.create_task(jobs.loop('sensor',jobs.collect,60)),
               asyncio.create_task(jobs.loop('weather',jobs.weather,3600)),
               asyncio.create_task(jobs.loop('maintenance',jobs.maintenance,3600))]
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
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
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
    return dict(jobs=jobs_status,readings=dict(counts),database_bytes=size,timezone=db.settings().get('timezone','Etc/UTC'),
                backup_scope='Local snapshots only; off-server backup is not configured',version=APP_VERSION)

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
    return [dict(r) for r in con.execute('''SELECT f.* FROM forecasts f WHERE valid>=? AND valid<?
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
    with db.connect() as con: rows=forecast_rows(con,now,now+3*86400,False)
    for row in rows: row['aqi']=aqi(row['pm25'])
    return {'points':rows}

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
                writer.writerow([("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for v in row])
            yield out.getvalue(); cursor=rows[-1]['ts']
    return StreamingResponse(chunks(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="indigo-readings.csv"'})

WEB=Path(os.getenv('WEB_DIR',str(Path(__file__).parent.parent/'web'/'dist')))
if WEB.exists():
    if (WEB/'assets').exists(): app.mount('/assets',StaticFiles(directory=WEB/'assets'),name='assets')
    @app.get('/{path:path}')
    def frontend(path:str):
        if path.startswith('api/') or path=='api': raise HTTPException(404)
        resolved=(WEB/path).resolve()
        if not resolved.is_relative_to(WEB.resolve()): raise HTTPException(404)
        if resolved.is_file(): return FileResponse(resolved)
        if '.' in path: raise HTTPException(404)
        return FileResponse(WEB/'index.html')
