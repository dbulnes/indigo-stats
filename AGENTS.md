# Indigo Stats agent instructions

These instructions apply to the entire repository.

## Repository purpose and boundaries

This repository owns the Indigo Stats application: the React/TypeScript PWA, Python FastAPI API and collector, SQLite schema and migrations, Dockerfile, runtime documentation, tests, CI, and image release workflow.

Unraid Community Applications metadata is maintained separately in [dbulnes/indigo-stats-unraid](https://github.com/dbulnes/indigo-stats-unraid). Do not add `ca_profile.xml`, Unraid template XML, CA icons, or submission-specific documentation here. Update the packaging repository only when ports, paths, variables, defaults, descriptions, icons, image coordinates, or other installation metadata change.

## Privacy and host safety

- Never commit a real street address, coordinates, sensor IP, homelab IP/hostname, tailnet URL, credentials, database, backup, or raw sensor payload.
- Private configuration belongs in environment variables or the persistent SQLite settings table. Browser APIs and logs must not reveal it.
- Do not deploy to, modify, scan, or remove anything from an Unraid server unless the user explicitly requests that host action in the current task.
- Do not add SSH, Docker socket, privileged mode, host networking, automatic Tailscale configuration, or host service changes to the container.
- Use synthetic data for CI and disposable smoke tests. Preserve real `/data` volumes unless deletion is explicitly requested.

## Architecture and persistence

- Run one Uvicorn worker because the web process owns the scheduled jobs.
- Store every mutable file under `/data`. The container image must remain replaceable without losing history.
- Keep SQLite on local storage rather than SMB/NFS. Preserve WAL/SHM files with the database when copying a live data directory.
- Use short transactions, WAL mode, `synchronous=FULL`, and the SQLite backup API.
- Take a consistent pre-migration backup, migrate transactionally, advance `PRAGMA user_version`, and refuse databases created by newer application schemas.
- Keep raw temperature and humidity so display conversions can change without rewriting history. EPA PM correction must use raw humidity.

## Development and verification

Use Python 3.12+ and Node 22+.

```sh
python -m unittest discover -s backend/tests -v
npm ci --prefix web
npm run build --prefix web
```

For a local Apple Silicon image:

```sh
docker build --platform linux/arm64 -t indigo-stats:mac-arm64 .
sh deploy/smoke-test.sh indigo-stats:mac-arm64
```

For the Unraid release architecture:

```sh
docker build --platform linux/amd64 -t indigo-stats:unraid-amd64 .
sh deploy/smoke-test.sh indigo-stats:unraid-amd64
```

The smoke test must use a disposable volume and verify non-root startup, health, persistence after container recreation, backup integrity, temperature conversion, and PWA assets. Do not point it at an installed appdata directory.

Make small, coherent commits. Run checks appropriate to the changed files before pushing. Every push and pull request runs one native AMD64 CI job matching Unraid; ordinary commits do not publish images. ARM64 container verification is local-only on Apple Silicon Macs.

## Release process

1. Choose a semantic version and run `node scripts/bump-version.mjs <major|minor|patch|X.Y.Z>`. The command updates `web/package.json`, `web/package-lock.json`, and `backend/app.py` together. Do not edit those versions separately.
2. Update release notes or user-facing documentation for material behavior, migration, configuration, or operational changes.
3. Commit to `main`, push, and wait for the `Build and test` workflow to pass.
4. Create an annotated, immutable tag `vX.Y.Z` on that exact tested `main` commit and push the tag. Never move or reuse a published release tag.
5. The tag-triggered release workflow publishes **only `linux/amd64`** to `ghcr.io/dbulnes/indigo-stats`, with version, major/minor, and (for stable releases) `latest` tags. It authenticates with GitHub Actions' short-lived `GITHUB_TOKEN`; do not add Docker Hub credentials or custom registry secrets.
6. Confirm the GHCR package remains public, inspect the manifest for `linux/amd64`, pull it anonymously, and run `deploy/smoke-test.sh ghcr.io/dbulnes/indigo-stats:latest`.

ARM64 images are for local Mac testing only. They are not built in per-commit CI or published as releases.

The release image index also contains a non-runnable `unknown/unknown` attestation manifest for its SBOM and build provenance. Preserve those attestations; they do not add a supported runtime architecture.

## How Unraid updates work

The CA template tracks `ghcr.io/dbulnes/indigo-stats:latest`. Publishing a new stable tag moves `latest` to a new image digest. Unraid detects that digest as an available container update. Users normally apply it from the Docker/Apps UI; publishing does not silently replace their running container unless they separately configure an auto-update plugin.

An update recreates the container from the new image while retaining the existing `/data` mapping and template settings. Before schema-changing releases, document backup and rollback implications. Never delete appdata during an update.

Application-only releases do not require editing or resubmitting the CA repository. When the Unraid template or repository metadata changes, update `indigo-stats-unraid`, run its `scripts/validate.py`, push it, and rerun CA Validate and Scan as required by the submission portal.
