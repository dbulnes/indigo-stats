"""Private local administration. Input JSON from stdin, never via command arguments."""
import argparse
import ipaddress
import json
import sys
from . import db
from .config import validate

parser=argparse.ArgumentParser()
parser.add_argument('action',choices=['configure','backup','check'])
args=parser.parse_args()
db.initialize()
if args.action=='configure':
    cfg=json.load(sys.stdin)
    allowed={'address','latitude','longitude','sensor_host','timezone','pm_method','temperature_offset_f','humidity_offset','placement','environment_mode','forecast_enabled'}
    if set(cfg)-allowed: raise SystemExit('Unknown configuration key')
    if 'sensor_host' in cfg:
        ip=ipaddress.ip_address(cfg['sensor_host'])
        if not ip.is_private or ip.is_loopback: raise SystemExit('Sensor must use a private LAN IP')
    for key,lo,hi in [('latitude',-90,90),('longitude',-180,180),('temperature_offset_f',-30,30),('humidity_offset',-30,30)]:
        if key in cfg and not lo<=float(cfg[key])<=hi: raise SystemExit('Invalid configuration value')
    if cfg.get('pm_method','cf1') not in ('cf1','epa2021'): raise SystemExit('Unknown correction')
    db.set_settings(validate(cfg))
    print('Private configuration saved. Values omitted.')
elif args.action=='backup':
    db.backup(); print('Consistent local backup created.')
else:
    with db.connect() as con:
        result=con.execute('PRAGMA integrity_check').fetchone()[0]
    if result!='ok': raise SystemExit('Integrity check failed')
    print('Database integrity: ok')
