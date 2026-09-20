"""Local storage. The complete DATA_DIR must be a persistent, local volume."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DATA = Path(os.getenv('DATA_DIR', '/data'))
SCHEMA_VERSION = 2

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
    if version > SCHEMA_VERSION:
        raise RuntimeError('Database is newer than this image. Restore a matching backup to roll back.')
    if version and version < SCHEMA_VERSION:
        backup()
    with connect() as con:
        con.execute('PRAGMA journal_mode=WAL')
        if version == 0:
            con.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE readings (
                    ts INTEGER PRIMARY KEY, source_ts TEXT,
                    temperature REAL, humidity REAL,
                    temperature_raw REAL, humidity_raw REAL,
                    pm_a REAL, pm_b REAL, pm25 REAL,
                    method TEXT NOT NULL, quality TEXT NOT NULL
                );
                CREATE TABLE raw_samples (ts INTEGER PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE forecasts (
                    fetched INTEGER NOT NULL, valid INTEGER NOT NULL,
                    kind TEXT NOT NULL, temperature REAL, humidity REAL, pm25 REAL,
                    PRIMARY KEY(kind, valid, fetched)
                );
                CREATE INDEX forecast_time ON forecasts(valid, kind, fetched DESC);
                CREATE TABLE job_status (
                    name TEXT PRIMARY KEY, last_attempt INTEGER, last_success INTEGER, error TEXT
                );
                CREATE TABLE summaries (
                    bucket INTEGER NOT NULL, span INTEGER NOT NULL, n INTEGER NOT NULL,
                    temperature REAL, humidity REAL, pm25 REAL,
                    temp_min REAL, temp_max REAL, pm_min REAL, pm_max REAL,
                    PRIMARY KEY(span,bucket)
                );
                PRAGMA user_version=1;
                COMMIT;
            ''')
        if version < 2:
            con.executescript('''
                BEGIN IMMEDIATE;
                ALTER TABLE readings ADD COLUMN environment_mode TEXT NOT NULL DEFAULT 'raw';
                PRAGMA user_version=2;
                COMMIT;
            ''')
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
