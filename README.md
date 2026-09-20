# Indigo Stats

A private air and weather observatory: React + TypeScript PWA, Python FastAPI, and SQLite, in one Unraid application container. Tailscale Serve provides private HTTPS. No Supabase, Google login, or public frontend hosting is required.

## What it does

- Polls a local PurpleAir sensor every minute; stores temperature (°F), humidity, both PM2.5 channels, quality flags, and corrected PM2.5.
- Calculates US AQI estimates using EPA 2024 breakpoints. Shows a PM2.5 NowCast once recent hourly data is sufficiently complete.
- Fetches hourly regional weather and air-quality forecasts from Open-Meteo; historical comparisons only use forecasts retrieved before their target hour.
- Offers date ranges, zoom/time navigation, previous-period overlays, threshold inspection, daily patterns, CSV export, and collector/storage health.
- Keeps minute history indefinitely by default. Raw sensor payloads expire after 30 days. Monitor actual disk usage.
- Makes consistent daily SQLite backups, retaining the latest 14 snapshots on the same persistent volume. Off-server backups are a separate setup step.

## Architecture and persistence

The image contains code and frontend assets. All mutable state is under `/data`, which must be bound to a persistent **local** Unraid appdata directory. Replacing the image does not replace the database. Mount the entire directory, including SQLite WAL/SHM files. Do not use an SMB/NFS mount for the live database.

```
Browser / installed PWA → Tailscale HTTPS → FastAPI → SQLite on appdata
                                           ↑
                         PurpleAir collector + forecast jobs
```

Use one Uvicorn worker: the application owns the scheduled collection jobs. The container runs as UID 10001, with a read-only root filesystem. It needs outbound access to the sensor and Open-Meteo. The web port is published only on host loopback. Tailnet access is the access boundary; do not publish the application port or use Tailscale Funnel.

## Local development

Requires Python 3.12+ and Node 22+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
npm ci --prefix web
npm run build --prefix web
DATA_DIR="$PWD/data" .venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

For frontend hot reload, run `npm run dev --prefix web`; Vite proxies `/api` to port 8765. Set `DISABLE_JOBS=1` for UI-only work. An empty database displays a clear waiting state; production does not fabricate historical readings.

## Unraid setup

1. Choose a persistent local SSD appdata directory and create it with ownership `10001:10001` and mode `700`.
2. Build the image: `docker build -t indigo-stats:local .`
3. Set `INDIGO_DATA_DIR` to that directory, then run `docker compose -f deploy/compose.yaml up -d`.
4. Set private configuration through stdin, using `deploy/private-config.example.json` as a template. Keep the actual file outside the repo:

```sh
docker exec -i indigo-stats python -m backend.manage configure < /path/to/private-config.json
docker restart indigo-stats
```

5. Inspect existing Tailscale Serve configuration and choose an unused HTTPS port. For example:

```sh
tailscale serve --bg --https=8443 http://127.0.0.1:8765
```

Open the resulting HTTPS URL from a device connected to your tailnet. Use the browser's install/Add to Home Screen action. Configure tailnet grants/ACLs before sharing access; the app does not provide user-level authorization. Preserve existing Unraid administration services.

Private settings include address, latitude/longitude, sensor IP, timezone, and calibration. They are stored in SQLite and never returned by dashboard endpoints. Do not commit actual settings, database files, backups, sensor payloads, or private server URLs. Forecast requests send coordinates to Open-Meteo, not the street address. Raw sensor responses can contain location/network metadata and therefore remain local only.

## Safe upgrades and restore

See [operations](docs/operations.md). The essential sequence is: build the new image, stop the application, make a consistent backup with the previous image, replace only the container, verify health and retained data. Keep the previous image and a pre-upgrade backup.

Schema migrations are versioned through `PRAGMA user_version`. Startup refuses to open a database from a newer schema version. Future migrations must run transactionally and take a pre-migration snapshot. Image rollback alone may not undo a database migration.

## Measurement interpretation

- The default PM method is raw CF=1 channel average. Outdoor installations can select `epa2021`, the published correction `max(0, 0.524 × CF1 − 0.0862 × RH + 5.75)`. It is **not** the extended wildfire correction; inputs above 500 µg/m³ are withheld with a quality flag rather than extrapolated. Raw channels remain available.
- Channel disagreement is flagged when the difference exceeds both 5 µg/m³ and 30% of the mean. A single available channel is retained and flagged.
- Temperature/humidity remain raw unless explicit offsets are configured. Internal sensor heating can bias readings. Polling every minute does not guarantee that the sensor updates every field each minute.
- NowCast uses up to 12 completed hourly means, requires at least 45 valid samples per included hour, and at least two valid hours among the latest three. Before that, the UI labels AQI as an interval estimate. AQI values above the scale are shown as 500+ for current readings.
- Regional AQI comparison is derived from modeled PM2.5 using the same breakpoints, not the overall AQI across all pollutants. Forecast retrieval time is recorded; it is not claimed to be model issuance time.

## Validation

```sh
.venv/bin/python -m unittest discover -s backend/tests -v
npm run build --prefix web
```

Tests cover AQI boundaries, channel correction, NowCast missing hours, duplicate ingestion, private-setting isolation, export pagination, forecast look-ahead prevention, backup integrity, and schema rollback protection.

Sources: [PurpleAir local JSON](https://community.purpleair.com/t/sensor-json-documentation/6917), [EPA correction study](https://amt.copernicus.org/articles/14/4617/2021/), [AQI technical guidance](https://document.airnow.gov/technical-assistance-document-for-the-reporting-of-daily-air-quailty.pdf), [Open-Meteo](https://open-meteo.com/en/docs), [CAMS air forecasts](https://open-meteo.com/en/docs/air-quality-api).
