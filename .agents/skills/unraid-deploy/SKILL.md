---
name: unraid-deploy
description: >-
  Trigger automated Docker container updates and deployments on the Unraid server
  (homelab) using the sshhomelab alias from ~/.zshrc. Use this skill after releasing
  a new version, when container updates on Unraid need to be automated without using
  the Unraid Web GUI, or to inspect/verify container status and logs on homelab.
---

# Unraid Container Deployment Skill

This skill automates what is normally done manually in the Unraid Docker Web UI: pulling the latest container image from GHCR, updating Tailscale container hook configurations, gracefully stopping and recreating the container using Unraid's user template (`my-IndigoStats.xml`), pruning orphaned images, and verifying API health.

## SSH Host & Alias Resolution

The skill dynamically resolves the user's `sshhomelab` alias configured in `~/.zshrc`:
```bash
alias sshhomelab='ssh <user>@<unraid-host>'
```

To execute commands on the Unraid server in non-interactive subshells:
```bash
zsh -i -c 'sshhomelab "<remote-command>"'
# Or directly using the evaluated alias:
eval "$(zsh -i -c 'alias sshhomelab' | sed -E "s/sshhomelab=['\"]?([^'\"]+)['\"]?/\1/") '<remote-command>'"
```

## How Unraid Container Updates Work

In the Unraid Web GUI, clicking "Update" or "Apply" executes Unraid's native dockerman script:
```bash
/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/update_container <ContainerName>
```

This script:
1. Locates the container template in `/boot/config/plugins/dockerMan/templates-user/my-<ContainerName>.xml`.
2. Pulls the latest image (`ghcr.io/dbulnes/indigo-stats:latest`).
3. Extracts Tailscale entrypoint/command hooks if Tailscale is enabled.
4. Gracefully stops the existing container (`docker stop`).
5. Removes the old container (`docker rm`).
6. Runs `docker run` with the exact template parameters, port mappings (`8765:8000`), volume mounts (`/mnt/user/appdata/indigo-stats:/data`), environment variables, capabilities (`--cap-add=NET_ADMIN`), and devices (`/dev/net/tun`).
7. Flushes Unraid docker caches and deletes old orphan images.
8. Keeps the Unraid Docker web UI completely in sync (no "Update Available" badges left behind).

## Automated Deployment Helper

A deployment helper script is provided at [`scripts/deploy-unraid.sh`](file:///Users/davidbulnes/git/indigo-stats/scripts/deploy-unraid.sh).

### Usage

1. **Deploy immediately**:
   ```bash
   scripts/deploy-unraid.sh IndigoStats
   ```

2. **Wait for GitHub Actions release workflow before deploying**:
   ```bash
   scripts/deploy-unraid.sh IndigoStats v0.7.2
   # Or auto-detect latest tag:
   scripts/deploy-unraid.sh IndigoStats --wait-latest
   ```

## Verification Steps

1. **Check container state and health**:
   ```bash
   zsh -i -c 'sshhomelab "docker ps -a --filter name=IndigoStats"'
   ```

2. **Check API health endpoint**:
   ```bash
   zsh -i -c 'sshhomelab "curl -s http://127.0.0.1:8765/api/health"'
   ```

3. **Check container logs**:
   ```bash
   zsh -i -c 'sshhomelab "docker logs IndigoStats --tail 50"'
   ```
