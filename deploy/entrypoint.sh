#!/bin/sh
set -eu
# Only the mounted application directory is initialized; no host services/networking.
PUID=${PUID:-99}
PGID=${PGID:-100}
case "$PUID:$PGID" in *[!0-9:]*|:*|*:) echo 'PUID/PGID must be numeric' >&2; exit 1;; esac
[ "$PUID" -gt 0 ] || { echo 'Choose a non-root PUID' >&2; exit 1; }
if [ "$(id -u)" = 0 ]; then
    mkdir -p /data/backups /data/recovery /data/secrets
    chown "$PUID:$PGID" /data /data/backups /data/recovery /data/secrets
    gosu "$PUID:$PGID" chmod 700 /data /data/backups /data/recovery /data/secrets
    for item in /data/indigo.sqlite /data/indigo.sqlite-wal /data/indigo.sqlite-shm /data/backups/*.sqlite /data/secrets/google-drive-token.json; do
        [ ! -e "$item" ] || chown "$PUID:$PGID" "$item"
    done
    exec gosu "$PUID:$PGID" "$@"
fi
exec "$@"
