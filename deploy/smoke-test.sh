#!/bin/sh
# Disposable development/CI volume only. Never targets an installed app.
set -eu
image=${1:-indigo-stats:test}
# Detect the built image so this test supports native ARM Macs and amd64 Unraid.
platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")
echo "Testing $image ($platform)"
name="indigo-smoke-$$"
volume="$name-data"
offsite_volume="$name-offsite"
cleanup() {
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
    docker volume rm "$offsite_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
docker volume create "$volume" >/dev/null
docker volume create "$offsite_volume" >/dev/null
# Model a host-mounted share already prepared for the container user. The app itself
# must never change ownership or mount remote storage.
docker run --rm --platform "$platform" --entrypoint chown -v "$offsite_volume:/offsite" "$image" 99:100 /offsite
start() {
    docker run -d --platform "$platform" --name "$name" --init \
        --read-only --tmpfs /tmp:size=64m,mode=1777 \
        --cap-drop=ALL --cap-add=CHOWN --cap-add=DAC_OVERRIDE \
        --cap-add=SETUID --cap-add=SETGID --security-opt=no-new-privileges:true \
        -e DISABLE_JOBS=1 -e PUID=99 -e PGID=100 \
        -v "$volume:/data" -v "$offsite_volume:/offsite" "$image" >/dev/null
    for attempt in $(seq 1 30); do
        if docker exec "$name" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" >/dev/null 2>&1; then return; fi
        sleep 1
    done
    docker logs "$name"
    exit 1
}
start
docker exec "$name" sh -c 'if touch /root-filesystem-must-stay-read-only 2>/dev/null; then exit 1; fi'
docker exec "$name" python -c "from pathlib import Path; pid=Path('/proc/1/task/1/children').read_text().split()[0]; s=Path('/proc/'+pid+'/status').read_text(); assert 'Uid:\t99\t99\t99\t99' in s, s"
docker exec -u 99:100 "$name" python -c "from backend import db; db.set_settings({'smoke_marker':'retained'}); from backend import jobs; jobs.store_reading({'SensorId':'synthetic','current_temp_f':81,'current_humidity':42,'pm2_5_cf_1':15,'pm2_5_cf_1_b':15},{},120); db.backup()"
docker stop -t 35 "$name" >/dev/null
docker rm "$name" >/dev/null
start
docker exec -u 99:100 "$name" python -c "from backend import db; assert db.settings()['smoke_marker']=='retained'; assert list((db.DATA/'backups').glob('*.sqlite'))"
docker exec -u 99:100 "$name" python -m backend.manage check
docker exec -i -u 99:100 "$name" python - <<'OFFSITE'
from pathlib import Path
from unittest.mock import patch
from backend import backups, db

backups.set_config({'provider': 'filesystem'})
assert backups.run(wait=True)
remote = Path('/offsite/indigo-stats')
(remote / 'unrelated.txt').write_text('preserve')
with patch('backend.db.time.strftime', return_value='20990101T000000Z'):
    db.backup()
backups.REMOTE_RETENTION = 1
assert backups.run(wait=True)
assert len(backups.remote_list()) == 1
assert (remote / 'unrelated.txt').read_text() == 'preserve'
OFFSITE
docker exec -u 99:100 "$name" python -c "from pathlib import Path; files=list(Path('/offsite/indigo-stats').glob('*.sqlite')); manifests=list(Path('/offsite/indigo-stats').glob('*.manifest.json')); assert len(files)==len(manifests)==1"
docker stop -t 35 "$name" >/dev/null
docker rm "$name" >/dev/null
start
docker exec -u 99:100 "$name" python -c "import sqlite3; from backend import backups; items=backups.remote_list(); assert len(items)==1; path=backups.fetch(items[0].filename); con=sqlite3.connect(path); assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; con.close()"
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
docker stop -t 35 "$name" >/dev/null
docker rm "$name" >/dev/null
# A configured filesystem destination must fail closed when /offsite is absent.
docker run --rm --platform "$platform" --init --cap-drop=ALL --cap-add=CHOWN --cap-add=DAC_OVERRIDE \
    --cap-add=SETUID --cap-add=SETGID --security-opt=no-new-privileges:true \
    --read-only --tmpfs /tmp:size=64m,mode=1777 \
    -e PUID=99 -e PGID=100 -v "$volume:/data" "$image" \
    python -c "from backend import db,backups; db.initialize(); p=backups.FilesystemProvider(); exec('try:\n p.probe(); raise SystemExit(1)\nexcept backups.BackupError:\n pass')"
echo 'PASS: verified off-server copy persisted and missing /offsite fails closed'
