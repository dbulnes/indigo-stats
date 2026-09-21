# Indigo Stats

[![Build and test](https://github.com/dbulnes/indigo-stats/actions/workflows/check.yml/badge.svg)](https://github.com/dbulnes/indigo-stats/actions/workflows/check.yml)

A private air and weather observatory: React + TypeScript PWA, Python FastAPI, and SQLite, in one Unraid application container. An optional private HTTPS proxy provides PWA access over your tailnet. No Supabase, Google login, or public frontend hosting is required.

## What it does

- Polls a local PurpleAir sensor every minute; stores temperature (°F), humidity, both PM2.5 channels, quality flags, and corrected PM2.5.
- Calculates US AQI estimates using EPA 2024 breakpoints. Shows a PM2.5 NowCast once recent hourly data is sufficiently complete.
- Optionally fetches hourly regional weather and air-quality forecasts from Open-Meteo; historical comparisons only use forecasts retrieved before their target hour.
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

Use one Uvicorn worker: the application owns the scheduled collection jobs. The entrypoint initializes permissions on the dedicated `/data` mount and drops privileges to PUID/PGID (Unraid defaults 99:100). The root filesystem is read-only. No Docker socket, host networking, SSH, host startup scripts, or automatic Tailscale configuration is required.

The Unraid template publishes a trusted-LAN port. The optional Compose example binds to loopback for a host reverse proxy. There is no application login: restrict access to your trusted LAN/tailnet and never publish it directly to the internet.

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

## Unraid application package

Community Applications metadata is maintained separately in [dbulnes/indigo-stats-unraid](https://github.com/dbulnes/indigo-stats-unraid). This repository owns the application source, Dockerfile, runtime documentation, tests, and image release workflow. The packaging repository owns the Unraid template, icon, CA profile, packaging license, and submission documentation.

When a public image is available, install it through Unraid using the separate template. Choose a dedicated persistent local appdata directory, enter your sensor's LAN IPv4 address and timezone, and select measurement methods. Container replacement must retain the same `/data` mapping. Neither repository deploys to a host automatically.

Forecasts default to disabled. To enable them, provide both private coordinates and set `FORECAST_ENABLED=true`. Coordinates are sent to Open-Meteo. Coordinates and raw sensor network metadata are omitted from dashboard APIs. Private settings are stored in SQLite; environment settings supplied on startup take precedence. Removing an environment value does not erase a stored setting; explicitly disable forecasts to stop requests.

Never commit filled templates, environment files, databases, backups, or real sensor payloads. Unraid administrators can inspect container variables and appdata even when template fields are masked. For CLI administration, pipe a private JSON file into `python -m backend.manage configure`; use `deploy/private-config.example.json` only as a generic starting point.

For mobile PWA installation, configure your own private HTTPS reverse proxy or Tailscale Serve separately. The application never changes those host services.

## Safe upgrades and restore

See [operations](docs/operations.md). The essential sequence is: build the new image, stop the application, make a consistent backup with the previous image, replace only the container, verify health and retained data. Keep the previous image and a pre-upgrade backup.

Schema migrations are versioned through `PRAGMA user_version`. Startup refuses to open a database from a newer schema version. Future migrations must run transactionally and take a pre-migration snapshot. Image rollback alone may not undo a database migration.

## Measurement interpretation

- The default PM method is raw CF=1 channel average. Outdoor installations can select `epa2021`, the published correction `max(0, 0.524 × CF1 − 0.0862 × RH + 5.75)`. It is **not** the extended wildfire correction; inputs above 500 µg/m³ are withheld with a quality flag rather than extrapolated. Raw channels remain available.
- Channel disagreement is flagged when the difference exceeds both 5 µg/m³ and 30% of the mean. A single available channel is retained and flagged.
- Temperature/humidity default to PurpleAir estimated ambient conversions. The dashboard can switch to raw operating readings or the simple −8°F / +4 humidity-point conversion. Raw values are retained, and PM correction always uses raw humidity. See [temperature correction](docs/temperature-correction.md). Polling every minute does not guarantee that the sensor updates every field each minute.
- NowCast uses up to 12 completed hourly means, requires at least 45 valid samples per included hour, and at least two valid hours among the latest three. Before that, the UI labels AQI as an interval estimate. AQI values above the scale are shown as 500+ for current readings.
- Regional AQI comparison is derived from modeled PM2.5 using the same breakpoints, not the overall AQI across all pollutants. Forecast retrieval time is recorded; it is not claimed to be model issuance time.

## GitHub Actions

[Build and test](https://github.com/dbulnes/indigo-stats/actions/workflows/check.yml) runs automatically on every push and pull request, with native Linux AMD64 (Unraid) and ARM64 (Apple Silicon Docker) jobs. Each job runs backend tests, builds the TypeScript/PWA frontend, builds the Docker image, and checks container startup, persistence, backups, and temperature output using synthetic data.

Push local commits to trigger CI: a push containing multiple commits builds its newest commit once. To run manually, open Actions → Build and test → Run workflow. Open an individual run and job to inspect logs. No extra credentials or secrets are needed for these checks. CI runs on GitHub-hosted machines and does not connect to your sensor or homelab.

Normal builds test images without publishing them. The separate release workflow publishes a `linux/amd64` image for Unraid to GitHub Container Registry only when a version tag is pushed. ARM64 images are built only for local Mac testing and per-commit CI. Publishing uses GitHub Actions' short-lived built-in `GITHUB_TOKEN` with `packages: write`; no Docker Hub account or repository secrets are required. After the first release, make the GHCR package public so Unraid can pull it anonymously.

Prepare a release version with one command from the repository root:

```sh
node scripts/bump-version.mjs patch
```

Use `major`, `minor`, `patch`, or an explicit semantic version such as `0.2.0`. The command updates the frontend package metadata, lockfile, and backend API version together. CI runs `node scripts/bump-version.mjs --check` to reject version drift.

## Validation

```sh
.venv/bin/python -m unittest discover -s backend/tests -v
npm run build --prefix web
```

Tests cover AQI boundaries, channel correction, NowCast missing hours, duplicate ingestion, private-setting isolation, export pagination, forecast look-ahead prevention, backup integrity, and schema rollback protection.

Sources: [PurpleAir local JSON](https://community.purpleair.com/t/sensor-json-documentation/6917), [EPA correction study](https://amt.copernicus.org/articles/14/4617/2021/), [AQI technical guidance](https://document.airnow.gov/technical-assistance-document-for-the-reporting-of-daily-air-quailty.pdf), [Open-Meteo](https://open-meteo.com/en/docs), [CAMS air forecasts](https://open-meteo.com/en/docs/air-quality-api).
