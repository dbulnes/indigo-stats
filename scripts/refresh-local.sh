#!/bin/sh
# Build, smoke-test, and replace the localhost-only development container.
# The named /data volume is retained across container replacement.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$repo_dir"

image=${INDIGO_LOCAL_IMAGE:-indigo-stats:local}
container=${INDIGO_LOCAL_CONTAINER:-indigo-stats-local}
data_volume=${INDIGO_LOCAL_DATA_VOLUME:-indigo-stats-local-data}
port=${INDIGO_LOCAL_PORT:-8765}
platform=${INDIGO_LOCAL_PLATFORM:-linux/arm64}

case "$port" in
    ''|*[!0-9]*) echo "INDIGO_LOCAL_PORT must be numeric" >&2; exit 2 ;;
esac

if docker container inspect "$container" >/dev/null 2>&1; then
    current_data=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{if .Name}}{{.Name}}{{else}}{{.Source}}{{end}}{{end}}{{end}}' "$container")
    if [ -z "$current_data" ] || [ "$current_data" != "$data_volume" ]; then
        echo "Refusing to replace $container: its /data mount is not $data_volume" >&2
        echo "Set INDIGO_LOCAL_DATA_VOLUME to the existing named volume or bind path." >&2
        exit 2
    fi
fi

echo "Building $image for $platform"
docker build --platform "$platform" -t "$image" .

echo "Running the required disposable smoke test"
sh deploy/smoke-test.sh "$image"

if docker container inspect "$container" >/dev/null 2>&1; then
    echo "Replacing $container; preserving $data_volume"
    docker stop -t 35 "$container" >/dev/null
    docker rm "$container" >/dev/null
fi

docker run -d --platform "$platform" --name "$container" --init \
    --restart unless-stopped \
    --read-only --tmpfs /tmp:size=64m,mode=1777 \
    --cap-drop=ALL --cap-add=CHOWN --cap-add=DAC_OVERRIDE \
    --cap-add=SETUID --cap-add=SETGID --security-opt=no-new-privileges:true \
    -p "127.0.0.1:$port:8000" \
    -e PUID=99 -e PGID=100 \
    -v "$data_volume:/data" "$image" >/dev/null

for attempt in $(seq 1 30); do
    if docker exec "$container" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4)" >/dev/null 2>&1; then
        echo "Indigo Stats is ready at http://127.0.0.1:$port"
        exit 0
    fi
    sleep 1
done

docker logs "$container"
echo "The refreshed container did not become healthy" >&2
exit 1
