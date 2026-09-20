# Unraid packaging and distribution

This is a development package, not a published Community Applications listing. No installation or host modification happens when code is committed. The container only owns its process and dedicated `/data` mount.

## Package contents

- `Dockerfile`: React assets and Python server in one Linux image.
- `deploy/entrypoint.sh`: initialize dedicated data permissions, then run as PUID/PGID.
- `templates/indigo-stats.xml`: bridge networking, persistent appdata, sensor/settings fields, and restricted runtime options.
- `ca_profile.xml`: Community Applications repository metadata.
- `.github/workflows/check.yml`: tests, frontend build, Docker build, and disposable-volume recreation smoke test.
- `.github/workflows/release.yml`: version-tag-triggered public image publishing to GHCR, for Linux amd64. No SSH or server deployment.

## Before publishing

1. Run backend tests and the frontend build. Build `docker build --platform linux/amd64 -t indigo-stats:test .` on a development Docker host.
2. Test the image with disposable synthetic data, the template's read-only root/capability options, and a dedicated local `/data` directory. Run `sh deploy/smoke-test.sh indigo-stats:test` to verify non-root application UID, health, backup, graceful shutdown, and recreation with the same data. Never use an existing server deployment for this test without its owner's instruction.
3. Review repository changes for private locations, sensor payloads, network addresses, and credentials. Leave all location and sensor defaults empty in the public template.
4. Publish the reviewed source, then intentionally push a version tag such as `v0.1.2` to trigger the release workflow. Ensure the GHCR package visibility is public and verify anonymous pulls. Tagged images let owners pin versions; `latest` tracks stable releases.
5. Confirm the template's image, icon, README, support, and raw XML URLs resolve publicly. A local draft references URLs that will not resolve until its files and image are published.
6. Use the [Community Apps submission portal](https://ca.unraid.net/submit), scan the repository, resolve validation findings, and submit for review. A listing is not guaranteed merely by adding XML. Follow the [official submission documentation](https://ca.unraid.net/submit/help) and [required repository profile format](https://ca.unraid.net/submit/help/repository-info-xml).

## Owner-controlled installation

Before a CA listing exists, the owner can configure Docker → Add Container using the mappings and variables in the XML, with a locally built or published image. Once listed, Apps supplies the template. Enter private values through Unraid, not by editing and publishing the repository template. HTTPS for PWA installation is configured independently by the owner.

Keep the `/data` appdata directory on image upgrades; see [operations](operations.md). Removing a container must not remove its appdata. Local SQLite snapshots are not off-server backups.

## Current verification limits

On September 20, 2026, the Linux amd64 image built and ran successfully on local ARM Docker Desktop using emulation. The disposable-volume smoke test passed non-root startup with the template's restricted runtime options, container replacement with retained settings and synthetic readings, SQLite backup integrity, estimated-temperature API output, and PWA asset delivery. All 13 backend tests also passed inside the image as UID 99 with a read-only root filesystem and networking disabled. Test containers and volumes were removed afterward.

This verifies local Docker behavior, not an Unraid UI installation, real sensor connectivity from Unraid, or phone PWA installation. Docker CI is provided but has not run for unpushed local commits. No image release, server deployment, or CA submission has been performed.
