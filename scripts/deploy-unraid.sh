#!/usr/bin/env bash
# Deploy / update container on Unraid homelab server via sshhomelab alias.
#
# Usage:
#   scripts/deploy-unraid.sh [CONTAINER_NAME]
#
# Examples:
#   scripts/deploy-unraid.sh
#   scripts/deploy-unraid.sh IndigoStats

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()  { printf "${CYAN}▸${RESET} %s\n" "$*"; }
ok()    { printf "${GREEN}✓${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${RESET} %s\n" "$*"; }
die()   { printf "${RED}✗${RESET} %s\n" "$*" >&2; exit 1; }

CONTAINER_NAME="${1:-IndigoStats}"

# Resolve SSH command via sshhomelab alias from ~/.zshrc or UNRAID_SSH env var
SSH_CMD="${UNRAID_SSH:-}"
if [ -z "$SSH_CMD" ] && [ -f "$HOME/.zshrc" ]; then
  ALIAS_TARGET="$(zsh -i -c 'alias sshhomelab' 2>/dev/null | sed -E "s/sshhomelab=['\"]?([^'\"]+)['\"]?/\1/" || true)"
  if [ -n "$ALIAS_TARGET" ]; then
    SSH_CMD="$ALIAS_TARGET"
  fi
fi

if [ -z "$SSH_CMD" ]; then
  die "Could not resolve sshhomelab alias from ~/.zshrc. Define 'alias sshhomelab=\"...\"' in ~/.zshrc or set UNRAID_SSH."
fi

# ── Trigger container update on Unraid ───────────────────────────────
info "Connecting to Unraid to update container '${CONTAINER_NAME}'…"
eval "$SSH_CMD 'test -f /usr/local/emhttp/plugins/dynamix.docker.manager/scripts/update_container'" || \
  die "Could not find Unraid update_container script on remote host"

info "Triggering native Unraid dockerman update for '${CONTAINER_NAME}'…"
eval "$SSH_CMD '/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/update_container \"$CONTAINER_NAME\"'"

ok "Unraid container update command completed"

# ── Verification ─────────────────────────────────────────────────────
info "Verifying container status on remote host…"
STATUS="$(eval "$SSH_CMD 'docker ps --filter name=^/${CONTAINER_NAME}\$ --format \"{{.Status}}\"'")"
if [ -z "$STATUS" ]; then
  die "Container '${CONTAINER_NAME}' is not running after update!"
fi
ok "Container is running: ${STATUS}"

info "Verifying API health check…"
HEALTH_OK=0
for i in $(seq 1 15); do
  HEALTH="$(eval "$SSH_CMD 'curl -s --connect-timeout 2 http://127.0.0.1:8765/api/health 2>/dev/null || curl -s --connect-timeout 2 http://127.0.0.1:8000/api/health 2>/dev/null'" || true)"
  if echo "$HEALTH" | grep -q '"ok":true'; then
    ok "IndigoStats API healthy: ${HEALTH}"
    HEALTH_OK=1
    break
  fi
  sleep 2
done

if [ "$HEALTH_OK" -ne 1 ]; then
  warn "Health check did not respond within 30s. Check logs on Unraid: eval \"$SSH_CMD 'docker logs ${CONTAINER_NAME} --tail 50'\""
fi

printf "\n${BOLD}${GREEN}Deployment of ${CONTAINER_NAME} to Unraid complete!${RESET}\n"
