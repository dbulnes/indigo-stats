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
COMMIT;
