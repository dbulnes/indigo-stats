# Operations

## Storage and privacy

Bind a dedicated local SSD appdata directory to `/data`. Keep ownership UID/GID 10001 and directory mode 700. This directory contains `indigo.sqlite`, WAL/SHM files, and `backups/`. Backups include the private configuration table and raw sensor metadata: never upload them unencrypted to a public location or commit them to Git.

SQLite uses WAL mode, foreign keys, a busy timeout, and `synchronous=FULL`. Short database transactions avoid blocking collection while charts are queried. The app has one scheduler and must run with one server worker.

Minute-level records are not automatically deleted. Raw payloads are retained for 30 days. Historical forecasts are reduced to the last available pre-target snapshot per hour after one day. Local daily snapshots retain the latest 14 files. Database disk usage and job failures are visible under System.

## Update without losing data

Build the next image while the existing app runs. Note the current image ID, data mount, and record count using `docker inspect` and `/api/status`. Then:

1. Stop the existing container with a 35-second grace period.
2. Run `python -m backend.manage backup` in a temporary container using the **previous image** and the same `/data` mount. This avoids running new migrations before the pre-upgrade snapshot.
3. Retain the previous container, stopped and renamed, or retain its image and launch configuration.
4. Start the new container with exactly the same data mount and the documented security/network options. Never remove the appdata directory as part of an image update.
5. Verify `/api/health`, `python -m backend.manage check`, the previous record count/history, and the next sensor sample. The app must not show a new empty database.
6. Once verified, the stopped previous container can be removed. Keep the old image and pre-upgrade snapshot until confident in the update.

Container recreation is reversible because state is outside the container. Do not run both old and new application containers simultaneously against the same volume: they would run duplicate schedulers.

## Restore

Stop the application. Preserve the entire existing data directory in a separate recovery location, including WAL/SHM files. Make a new empty data directory with appropriate ownership, copy the selected consistent backup into it as `indigo.sqlite`, and launch the matching image using that directory. Do not combine a restored database file with WAL files from another database state. Run the integrity check and inspect historical counts before resuming normal operation.

A daily backup can lose up to approximately 24 hours of records. Local snapshots protect against some software/operator mistakes but not loss of the server or its disks. Off-server encrypted backups are intentionally pending a destination decision. Parity is not a backup. Test a restore into an isolated directory periodically.

## Tailscale and PWA

Use host Tailscale Serve with HTTPS forwarding to the loopback application port. Do not enable Funnel. Test the exact HTTPS URL from a phone connected to the tailnet. A browser pointed at an ordinary HTTP LAN IP does not have the same PWA installation capabilities.

The static app shell is cached by the service worker. API responses are not service-worker cached. A disconnected open page retains its currently displayed values with an offline/connection warning; a fresh offline launch may have no measurements. Reconnect to the tailnet to fetch current data. A new frontend version activates after old app tabs/windows close, avoiding a forced reload mid-use.

## Troubleshooting

- **Sensor timeouts:** verify its private runtime IP and reachability from the container, not just another LAN device. Check device Wi-Fi signal, HTTP responsiveness, DHCP address changes, and host routing. Do not fill collection gaps with repeated or synthetic samples.
- **Channels disagree:** inspect the raw A/B values and quality flags. Corrections do not fix a failing particle counter.
- **Forecast missing:** verify privately configured coordinates and outbound HTTPS connectivity. The System page reports failures without revealing request URLs or location.
- **NowCast missing:** enough complete hourly history has not accumulated. Interval AQI is still available and explicitly labeled.
- **Historical forecast missing:** no prediction was stored before that hour. The app intentionally avoids presenting a later forecast as an earlier prediction.
- **Backups failing:** inspect volume free space and ownership. The health endpoint checks the database, while per-job errors are separate on System.

Logs omit sensor payloads, coordinates, address, provider URLs, and connection credentials. The API is read-only; private administration uses a local CLI via stdin.
