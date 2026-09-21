BEGIN IMMEDIATE;
CREATE TABLE backup_transfers (
    destination TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    checksum TEXT NOT NULL,
    size INTEGER NOT NULL,
    remote_reference TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt INTEGER,
    completed_at INTEGER,
    error TEXT,
    PRIMARY KEY(destination, snapshot)
);
CREATE INDEX backup_transfer_completed ON backup_transfers(destination, completed_at DESC);
COMMIT;
