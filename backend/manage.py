"""Private local administration. Input JSON from stdin, never via command arguments."""
import argparse
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
    allowed={'address','latitude','longitude','sensor_host','timezone','pm_method','placement','environment_mode','forecast_enabled'}
    if set(cfg)-allowed: raise SystemExit('Unknown configuration key')
    try:
        validate(db.settings()|cfg)
    except (ValueError, TypeError, KeyError):
        raise SystemExit('Invalid private configuration') from None
    db.set_settings(cfg)
    print('Private configuration saved. Values omitted.')
elif args.action=='backup':
    db.backup(); print('Consistent local backup created.')
else:
    with db.connect() as con:
        result=con.execute('PRAGMA integrity_check').fetchone()[0]
    if result!='ok': raise SystemExit('Integrity check failed')
    print('Database integrity: ok')
