#!/bin/sh
set -eu
# Only the mounted application directory is initialized; no host services/networking.
PUID=${PUID:-99}
PGID=${PGID:-100}
case "$PUID:$PGID" in *[!0-9:]*|:*|*:) echo 'PUID/PGID must be numeric' >&2; exit 1;; esac
[ "$PUID" -gt 0 ] || { echo 'Choose a non-root PUID' >&2; exit 1; }
if [ "$(id -u)" = 0 ]; then
    [ ! -L /data ] || { echo '/data must not be a symlink' >&2; exit 1; }
    for directory in /data/backups /data/recovery /data/secrets; do
        [ ! -L "$directory" ] || { echo "$directory must not be a symlink" >&2; exit 1; }
        mkdir -p "$directory"
        [ -d "$directory" ] || { echo "$directory must be a directory" >&2; exit 1; }
    done
    chown "$PUID:$PGID" /data /data/backups /data/recovery /data/secrets
    gosu "$PUID:$PGID" chmod 700 /data /data/backups /data/recovery /data/secrets
    for item in /data/indigo.sqlite /data/indigo.sqlite-wal /data/indigo.sqlite-shm \
        /data/backups/*.sqlite /data/recovery/*.sqlite /data/recovery/*.manifest.json \
        /data/secrets/google-drive-token.json; do
        [ ! -L "$item" ] || { echo "$item must not be a symlink" >&2; exit 1; }
        if [ -e "$item" ]; then
            chown "$PUID:$PGID" "$item"
            gosu "$PUID:$PGID" chmod 600 "$item"
        fi
    done
    exec gosu "$PUID:$PGID" "$@"
fi
exec "$@"
