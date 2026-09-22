# Operations

## Storage and privacy

Bind a dedicated local SSD appdata directory to `/data`. The entrypoint sets ownership to PUID/PGID (default 99:100) and directory mode 700 before dropping privileges. Use a dedicated app directory, never a shared parent directory. This directory contains `indigo.sqlite`, WAL/SHM files, and `backups/`. Backups include the private configuration table and raw sensor metadata: never upload them unencrypted to a public location or commit them to Git.

SQLite uses WAL mode, foreign keys, a busy timeout, and `synchronous=FULL`. Short database transactions avoid blocking collection while charts are queried. The app has one scheduler and must run with one server worker.

Minute-level records are not automatically deleted. Raw payloads are retained for 30 days. Historical forecasts are reduced to the last available pre-target snapshot per hour after one day. Local daily snapshots retain the latest 14 files. A configured off-server destination retains 30 completed snapshots. Database disk usage and job failures are visible under System.

## Off-server backups

The daily local SQLite snapshot and its integrity check remain the source of every remote copy. An independent hourly job reconciles retained local snapshots that are missing from the selected destination, newest first. It uploads the database first and a SHA-256 manifest last; a copy is complete only when its manifest and object metadata verify. Remote failures do not make the local backup fail. One destination can be active at a time, and switching destinations uses a separate transfer-history fingerprint.

Snapshots include the private settings table and raw sensor metadata. Indigo Stats does **not** add client-side encryption in this release. Use provider/filesystem access controls and encryption at rest, keep destinations private, and protect all credentials. The System API and UI expose only configured/readiness flags, never bucket names, endpoints, paths, OAuth callback URLs, folder IDs, tokens, or remote object references.

### S3 and compatible storage

Choose S3 in System and enter the bucket, optional prefix/region, optional custom endpoint, and encryption mode. Custom endpoints must use HTTPS. Supply credentials through `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`, and optional `BACKUP_S3_SESSION_TOKEN`, or the corresponding `_FILE` variables. For SSE-KMS, optionally supply `BACKUP_S3_KMS_KEY_ID` (or `_FILE`). Grant only bucket listing and get/put/delete access beneath the selected prefix. Provider-default encryption, SSE-S3, and SSE-KMS are supported. Indigo Stats records the SHA-256 in object metadata and verifies size and metadata after upload.

For step-by-step setup, least-privilege IAM policies, and provider examples (AWS, Cloudflare R2, MinIO, Backblaze B2), see the [S3 Backup Setup Guide](s3-backup-setup.md).

### Google Drive

Create a Google OAuth web client and supply `BACKUP_GOOGLE_CLIENT_ID`, `BACKUP_GOOGLE_CLIENT_SECRET`, and `BACKUP_GOOGLE_CALLBACK_URI` (or `_FILE`). The callback must exactly match the registered stable private HTTPS URL, except localhost may use HTTP. Select Google Drive, save, then use **Link Google Drive** (which opens a dedicated popup modal for authorization and automatically refreshes on completion). Authorization requests offline access, PKCE, CSRF state, and only the `drive.file` scope. Indigo Stats creates a visible app-owned “Indigo Stats Backups” folder and rediscovers it using private app properties. The refresh token is mode `0600` at `/data/secrets/google-drive-token.json`, outside SQLite and its snapshots. A revoked grant produces a relink-required error. Disaster recovery requires the same OAuth project followed by relinking.

For step-by-step Google Cloud Console configuration and troubleshooting, see the [Google Drive Backup Setup Guide](google-drive-backup-setup.md).

### Mounted filesystem

Mount NFS, SMB/CIFS, SSHFS, or another filesystem on the host, then bind that already-mounted directory to `/offsite` read/write. The optional Compose line demonstrates the bind. The app never mounts network storage, stores share credentials, creates `/offsite` as a fallback, or changes host services. `/offsite` must be a distinct mount writable by PUID/PGID. Files go only beneath `/offsite/indigo-stats`, via a same-directory partial file, `fsync`, atomic rename, and full read-back checksum. Symlinks and traversal are rejected. The live `/data/indigo.sqlite` must remain on local storage.

Use **Test connection** for a destination probe and **Back up now** to queue asynchronous reconciliation. A second manual request returns conflict while a run is active. Mount/provider outages are reported with sanitized errors and retried hourly. Pruning starts only after a verified upload and deletes only Indigo Stats snapshot/manifest names; unrelated remote files are untouched.

## Update without losing data

Pull or build the next image while the existing app runs. Note the current image ID, data mount, and record count using `docker inspect` and `/api/status`. Then:

1. Stop the existing container with a 35-second grace period.
2. Run `python -m backend.manage backup` in a temporary container using the **previous image** and the same `/data` mount. This avoids running new migrations before the pre-upgrade snapshot.
3. Retain the previous container, stopped and renamed, or retain its image and launch configuration.
4. Start the new container with exactly the same data mount and the documented security/network options. Never remove the appdata directory as part of an image update.
5. Verify `/api/health`, `python -m backend.manage check`, the previous record count/history, and the next sensor sample. The app must not show a new empty database.
6. Once verified, the stopped previous container can be removed. Keep the old image and pre-upgrade snapshot until confident in the update.

Container recreation is reversible because state is outside the container. Do not run both old and new application containers simultaneously against the same volume: they would run duplicate schedulers.

## Restore

Stop the application. Preserve the entire existing data directory in a separate recovery location, including WAL/SHM files. Make a new empty data directory with appropriate ownership, copy the selected consistent backup into it as `indigo.sqlite`, and launch the matching image using that directory. Do not combine a restored database file with WAL files from another database state. Run the integrity check and inspect historical counts before resuming normal operation.

A daily backup can lose up to approximately 24 hours of records. Parity is not a backup. Test a restore into an isolated directory periodically.

List complete remote snapshots without exposing provider references:

```sh
python -m backend.manage remote-list
python -m backend.manage remote-fetch 20260921T120000Z.sqlite
```

`remote-fetch` downloads and verifies the checksum and SQLite integrity, then writes the database and manifest under `/data/recovery`; it refuses overwrite and never replaces the live database. For total-loss S3/filesystem recovery before settings have been restored, provide the same non-secret destination object on stdin with `--config-stdin`; credentials still come from environment or secret files. For example: `printf '%s\n' '{"provider":"filesystem"}' | python -m backend.manage remote-list --config-stdin`. After fetching, stop the app, preserve the damaged data directory including WAL/SHM files, copy the verified recovery database into a new empty local data directory as `indigo.sqlite`, and start the matching application image. Never combine it with WAL/SHM files from another database state.

## Tailscale and PWA

Configure a private HTTPS proxy separately if desired. The Compose example binds to loopback; the Unraid template publishes to the trusted LAN. Restrict that port appropriately for your network. Do not enable Funnel. Test the exact HTTPS URL from a phone connected to the tailnet. A browser pointed at an ordinary HTTP LAN IP does not have the same PWA installation capabilities.

The static app shell is cached by the service worker. API responses are not service-worker cached. A disconnected open page retains its currently displayed values with an offline/connection warning; a fresh offline launch may have no measurements. Reconnect to the tailnet to fetch current data. A new frontend version activates after old app tabs/windows close, avoiding a forced reload mid-use.

## Troubleshooting

- **Sensor timeouts:** verify its private runtime IP and reachability from the container, not just another LAN device. Check device Wi-Fi signal, HTTP responsiveness, DHCP address changes, and host routing. Do not fill collection gaps with repeated or synthetic samples.
- **Channels disagree:** inspect the raw A/B values and quality flags. Corrections do not fix a failing particle counter.
- **Forecast missing:** verify forecasts are enabled, both privately configured coordinates, and outbound HTTPS connectivity. The System page reports failures without revealing request URLs or location.
- **NowCast missing:** enough complete hourly history has not accumulated. Interval AQI is still available and explicitly labeled.
- **Historical forecast missing:** no prediction was stored before that hour. The app intentionally avoids presenting a later forecast as an earlier prediction.
- **Local backups failing:** inspect local `/data` free space and ownership. The health endpoint checks the database, while per-job errors are separate on System.
- **Off-server backups failing:** use the System connection test; check provider permissions/credentials, OAuth link state, or that `/offsite` is still a distinct writable host mount. Failures retry hourly without affecting local snapshots.

Logs omit sensor payloads, coordinates, address, provider URLs, and connection credentials. Private administration uses container settings, the System backup controls, or a local CLI via stdin. Supplied environment settings override matching CLI settings at the next startup.
