"""Private local administration. Input JSON from stdin, never via command arguments."""
import argparse
import json
import sys
from . import db,backups
from .config import validate

def main(argv=None, input_stream=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['configure','backup','check','remote-list','remote-fetch'])
    parser.add_argument('snapshot',nargs='?')
    parser.add_argument('--config-stdin',action='store_true',help='Read destination configuration JSON from stdin')
    args=parser.parse_args(argv); source=input_stream or sys.stdin
    db.initialize()
    if args.action=='configure':
        cfg=json.load(source)
        allowed={
            'address','latitude','longitude','sensor_host','timezone','pm_method',
            'placement','environment_mode','forecast_enabled','units',
            'purpleair_sensor_index','purpleair_api_key','purpleair_read_key','sensor_source'
        }
        if set(cfg)-allowed: raise SystemExit('Unknown configuration key')
        try: validated=validate(db.settings()|cfg)
        except (ValueError, TypeError, KeyError): raise SystemExit('Invalid private configuration') from None
        db.set_settings({key:validated[key] for key in cfg})
        print('Private configuration saved. Values omitted.')
    elif args.action=='backup':
        db.backup(); print('Consistent local backup created.')
    elif args.action=='check':
        with db.connect() as con: result=con.execute('PRAGMA integrity_check').fetchone()[0]
        if result!='ok': raise SystemExit('Integrity check failed')
        print('Database integrity: ok')
    elif args.action=='remote-list':
        cfg=json.load(source) if args.config_stdin else None
        try: items=backups.remote_list(cfg)
        except backups.BackupError as exc: raise SystemExit(str(exc)) from None
        for item in items: print(f'{item.filename}\t{item.size}\t{item.checksum}')
    elif args.action=='remote-fetch':
        if not args.snapshot: raise SystemExit('remote-fetch requires a snapshot filename')
        cfg=json.load(source) if args.config_stdin else None
        try: path=backups.fetch(args.snapshot,cfg)
        except backups.BackupError as exc: raise SystemExit(str(exc)) from None
        print(f'Verified recovery snapshot written to {path}. The live database was not changed.')

if __name__=='__main__': main()
