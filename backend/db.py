"""Local storage. The complete DATA_DIR must be a persistent, local volume."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DATA = Path(os.getenv('DATA_DIR', '/data'))

@contextmanager
def connect():
    con = sqlite3.connect(DATA / 'indigo.sqlite', timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=20000')
    con.execute('PRAGMA synchronous=FULL')
    try:
        with con:
            yield con
    finally:
        con.close()

def backup():
    folder = DATA / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '.sqlite')
    with connect() as source:
        target = sqlite3.connect(path)
        try:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup integrity verification failed')
        finally:
            target.close()
    os.chmod(path, 0o600)
    for old in sorted(folder.glob('*.sqlite'))[:-14]:
        old.unlink()
    return path

def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA, 0o700)
    
    with connect() as con:
        version = con.execute('PRAGMA user_version').fetchone()[0]
    
    migrations_dir = Path(__file__).parent / 'migrations'
    migrations = sorted(migrations_dir.glob('*.sql'))
    target_version = len(migrations)
    
    if version > target_version:
        raise RuntimeError('Database is newer than this image. Restore a matching backup to roll back.')
    if version and version < target_version:
        backup()
        
    with connect() as con:
        con.execute('PRAGMA journal_mode=WAL')
        for i in range(version, target_version):
            script_path = migrations[i]
            with open(script_path, 'r', encoding='utf-8') as f:
                con.executescript(f.read())
            con.execute(f'PRAGMA user_version={i + 1}')
            
    os.chmod(DATA / 'indigo.sqlite', 0o600)


def settings():
    with connect() as con:
        return {r['key']: json.loads(r['value']) for r in con.execute('SELECT * FROM settings')}

def set_settings(values):
    with connect() as con:
        con.executemany('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        [(k,json.dumps(v)) for k,v in values.items()])

def status(name, error=None, success=False):
    now = int(time.time())
    with connect() as con:
        con.execute('''INSERT INTO job_status VALUES (?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET last_attempt=excluded.last_attempt,
            last_success=COALESCE(excluded.last_success,job_status.last_success),error=excluded.error''',
            (name,now,now if success else None,error))
