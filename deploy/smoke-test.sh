#!/bin/sh
# Disposable development/CI volume only. Never targets an installed app.
set -eu
image=${1:-indigo-stats:test}
name="indigo-smoke-$$"
volume="$name-data"
cleanup() {
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
docker volume create "$volume" >/dev/null
start() {
    docker run -d --platform linux/amd64 --name "$name" --read-only --tmpfs /tmp --init \
        --cap-drop=ALL --cap-add=CHOWN --cap-add=DAC_OVERRIDE \
        --cap-add=SETUID --cap-add=SETGID --security-opt=no-new-privileges:true \
        -e DISABLE_JOBS=1 -e PUID=99 -e PGID=100 \
        -v "$volume:/data" "$image" >/dev/null
    for attempt in $(seq 1 30); do
        if docker exec "$name" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" >/dev/null 2>&1; then return; fi
        sleep 1
    done
    docker logs "$name"
    exit 1
}
start
docker exec "$name" python -c "from pathlib import Path; pid=Path('/proc/1/task/1/children').read_text().split()[0]; s=Path('/proc/'+pid+'/status').read_text(); assert 'Uid:\t99\t99\t99\t99' in s, s"
docker exec -u 99:100 "$name" python -c "from backend import db; db.set_settings({'smoke_marker':'retained'}); from backend import jobs; jobs.store_reading({'SensorId':'synthetic','current_temp_f':81,'current_humidity':42,'pm2_5_cf_1':15,'pm2_5_cf_1_b':15},{},120); db.backup()"
docker stop -t 35 "$name" >/dev/null
docker rm "$name" >/dev/null
start
docker exec -u 99:100 "$name" python -c "from backend import db; assert db.settings()['smoke_marker']=='retained'; assert list((db.DATA/'backups').glob('*.sqlite'))"
docker exec -u 99:100 "$name" python -m backend.manage check
docker exec -i -u 99:100 "$name" python - <<'CHECK'
import json
import sqlite3
import urllib.request
from backend import db

def get(path):
    with urllib.request.urlopen('http://127.0.0.1:8000' + path) as response:
        return response.read()

points = json.loads(get('/api/history?start=0&end=180&environment=purpleair'))['points']
assert len(points) == 1
assert abs(points[0]['temperature'] - 73.4637) < 0.0001
assert points[0]['pm25'] == 15
assert '<html' in get('/').decode().lower()
manifest = json.loads(get('/manifest.webmanifest'))
assert manifest['display'] == 'standalone'
assert get('/sw.js')
with sqlite3.connect(next((db.DATA / 'backups').glob('*.sqlite'))) as backup:
    assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert backup.execute('SELECT temperature_raw FROM readings').fetchone()[0] == 81
print('PASS: non-root startup, persisted readings/config, backup integrity, estimated temperature, and PWA assets')
CHECK
