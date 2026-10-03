#!/usr/bin/env bash
# Local Docker only. Never reuses an existing container or volume.
set +x
set -euo pipefail
port=${1:-8877}
if [[ $# -gt 1 || ! "$port" =~ ^[0-9]+$ || ${#port} -gt 5 ]] ||
    (( 10#$port < 1024 || 10#$port > 65535 )); then
    echo "Usage: bash deploy/clothing-local-test.sh [localhost-port: 1024..65535]" >&2
    exit 1
fi
port=$((10#$port))
cd "$(dirname "$0")/.."
image=indigo-stats:clothing-test
# DOCKER_CONTEXT takes precedence over DOCKER_HOST in the Docker CLI.
if [[ -n ${DOCKER_CONTEXT:-} ]]; then
    endpoint=$(docker context inspect "$DOCKER_CONTEXT" --format '{{.Endpoints.docker.Host}}')
else
    endpoint=${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}
fi
case "$endpoint" in
    unix://*) ;;
    *) echo "Refusing a non-local Docker endpoint. Select local Docker Desktop first." >&2; exit 1 ;;
esac
platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")
if [[ ! ${CLOTHING_JEVMODEL_API_KEY+x} ]]; then
    read -r -s -p "JevModel key (hidden; Enter for no-key UI test): " CLOTHING_JEVMODEL_API_KEY
    printf '\n'
fi
export CLOTHING_JEVMODEL_API_KEY
name="indigo-clothing-test-$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
volume="$name-data"
created_volume=0
created_container=0
success=0
cleanup() {
    if (( ! success )); then
        if (( created_container )); then docker rm -f "$name" >/dev/null 2>&1 || true; fi
        if (( created_volume )); then docker volume rm "$volume" >/dev/null 2>&1 || true; fi
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if docker volume inspect "$volume" >/dev/null 2>&1 || docker container inspect "$name" >/dev/null 2>&1; then
    echo "Refusing an existing trial resource name." >&2
    exit 1
fi
docker volume create --label indigo.clothing.synthetic=1 "$volume" >/dev/null
created_volume=1
docker create --platform "$platform" --name "$name" --init \
    --read-only --tmpfs /tmp:size=64m,mode=1777 \
    --cap-drop=ALL --cap-add=CHOWN --cap-add=DAC_OVERRIDE \
    --cap-add=SETUID --cap-add=SETGID --security-opt=no-new-privileges:true \
    --label indigo.clothing.synthetic=1 \
    -e DISABLE_JOBS=1 -e PUID=99 -e PGID=100 \
    -e CLOTHING_SYNTHETIC_DISPOSABLE=indigo-clothing-disposable-v1 \
    -e CLOTHING_JEVMODEL_API_KEY \
    -p "127.0.0.1:$port:8000" -v "$volume:/data" "$image" >/dev/null
created_container=1
unset CLOTHING_JEVMODEL_API_KEY
docker start "$name" >/dev/null
ready=0
for (( attempt=0; attempt<60; attempt++ )); do
    if docker exec -u 99:100 "$name" python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=2)" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if (( ! ready )); then
    echo "Trial did not become healthy; cleaning up its disposable resources." >&2
    exit 1
fi
docker exec -i -u 99:100 "$name" python - < deploy/clothing-test-weather.py
success=1
printf '\nReady: http://127.0.0.1:%s (synthetic UTC weather only)\n' "$port"
printf 'No AI request was made. Browser Generate is deliberate and may incur charges.\n'
printf '\nRefresh evidence from the repository root before Generate:\n'
printf 'docker exec -i -u 99:100 %q python - < deploy/clothing-test-weather.py\n' "$name"
printf '\nCleanup only this trial when finished:\n'
printf 'docker rm -f %q && docker volume rm %q\n' "$name" "$volume"
