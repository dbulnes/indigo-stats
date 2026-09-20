# Implementation plan — accepted architecture

The current architecture is React/TypeScript PWA + Python FastAPI and jobs + embedded SQLite in one Unraid application container. All data lives on a persistent local appdata mount. Host Tailscale Serve provides private HTTPS. Supabase, GitHub Pages, Google SSO, invite codes, and alerts are out of scope.

## Deliverables

- Local sensor polling at 60-second intervals with raw channel preservation, configurable correction, quality flags, and timestamped history.
- SQLite WAL persistence, idempotent minute inserts, raw-payload retention, hourly/daily summaries, versioned schema, daily consistent snapshots, and explicit restore procedures.
- Open-Meteo weather and regional PM2.5 forecasts using privately stored coordinates. Historical forecasts are selected without look-ahead.
- Responsive dashboard for live readings, time ranges, visual comparisons, daily patterns, thresholds, CSV export, and system status.
- One non-root application container, loopback-only HTTP port, persistent appdata mount, and private tailnet HTTPS.
- Address and sensor network metadata absent from committed files and browser APIs.

## Validation and handoff

Test normalization, AQI, NowCast completeness, forecast selection, export completeness, private-data isolation, backup/restore, schema version safety, responsive layouts, and actual container recreation against the same persisted database. Verify real successive sensor samples and forecast requests; report any remaining connectivity limitations honestly.

Off-server encrypted backups are deferred until the owner chooses a destination. See README and operations.md for deployment, measurement methods, and operational limits.
