#!/usr/bin/env bash
# Automate the Indigo Stats release process.
#
# Usage:
#   sh scripts/release.sh <major|minor|patch|X.Y.Z>
#
# Steps performed:
#   1. Validate preconditions (clean tree, on main, up to date)
#   2. Bump version in web/package.json, web/package-lock.json, backend/app.py
#   3. Commit the version files
#   4. Push the commit to origin/main
#   5. Wait for the "Build and test" CI workflow to pass
#   6. Create an annotated tag vX.Y.Z and push it (triggers the release workflow)

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

# ── Argument ────────────────────────────────────────────────────────
BUMP="${1:-}"
[ -z "$BUMP" ] && die "Usage: sh scripts/release.sh <major|minor|patch|X.Y.Z>"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ── Preconditions ───────────────────────────────────────────────────
info "Checking preconditions…"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "main" ] || die "Must be on main branch (currently on $BRANCH)"

if ! git diff --quiet || ! git diff --cached --quiet; then
  die "Working tree is dirty — commit or stash changes first"
fi

git fetch origin --tags --quiet
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main 2>/dev/null || echo "")"
if [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
  die "Local main ($LOCAL) differs from origin/main ($REMOTE) — pull or push first"
fi

ok "On main, clean tree, up to date with origin"

# ── Version bump ────────────────────────────────────────────────────
info "Bumping version ($BUMP)…"
BUMP_OUTPUT="$(node scripts/bump-version.mjs "$BUMP")"
echo "  $BUMP_OUTPUT"

# Extract the new version from bump-version output or package.json
VERSION="$(node -e "process.stdout.write(require('./web/package.json').version)")"
TAG="v${VERSION}"

# Guard against duplicate tags
if git rev-parse "$TAG" >/dev/null 2>&1; then
  die "Tag $TAG already exists — choose a different version"
fi

ok "Version set to $VERSION"

# ── Commit and push ─────────────────────────────────────────────────
info "Committing version files…"
git add web/package.json web/package-lock.json backend/app.py
git commit -m "Release ${VERSION}"
ok "Committed: Release ${VERSION}"

info "Pushing commit to origin/main…"
git push origin main
ok "Commit pushed"

# ── Wait for CI ─────────────────────────────────────────────────────
COMMIT_SHA="$(git rev-parse HEAD)"
SHORT_SHA="$(git rev-parse --short HEAD)"

if command -v gh >/dev/null 2>&1; then
  info "Waiting for CI to pass on ${SHORT_SHA}…"
  printf "  (this polls GitHub Actions — press Ctrl-C to skip and tag manually)\n"

  # Poll until the CI run for our commit appears (GitHub may need a few seconds)
  RUN_ID=""
  for attempt in $(seq 1 30); do
    RUN_ID="$(gh run list --branch main --workflow check.yml --limit 5 \
      --json databaseId,headSha \
      --jq ".[] | select(.headSha == \"$COMMIT_SHA\") | .databaseId" 2>/dev/null | head -1 || echo "")"
    if [ -n "$RUN_ID" ] && [ "$RUN_ID" != "null" ]; then
      break
    fi
    RUN_ID=""
    if [ "$attempt" -lt 30 ]; then
      printf "  Waiting for CI run to appear (attempt %s/30)…\n" "$attempt"
      sleep 10
    fi
  done

  if [ -n "$RUN_ID" ]; then
    if gh run watch --exit-status "$RUN_ID"; then
      ok "CI passed"
    else
      die "CI failed — fix the issue before tagging"
    fi
  else
    die "Could not locate CI run for ${SHORT_SHA} after 5 minutes — verify at https://github.com/dbulnes/indigo-stats/actions"
  fi
else
  warn "GitHub CLI (gh) not installed — skipping CI wait"
  printf "  Install with: brew install gh\n"
  printf "  Verify CI passes at: https://github.com/dbulnes/indigo-stats/actions\n"
  printf "\n"
  read -r -p "$(printf "${YELLOW}Continue with tagging? [y/N]${RESET} ")" CONFIRM
  case "$CONFIRM" in
    [yY]*) ;;
    *) die "Aborted — tag manually after CI passes: git tag -a $TAG -m 'Release $VERSION' && git push origin $TAG" ;;
  esac
fi

# ── Tag and push ────────────────────────────────────────────────────
info "Creating annotated tag ${TAG}…"
git tag -a "$TAG" -m "Release ${VERSION}"
ok "Tag created"

info "Pushing tag ${TAG} to origin…"
git push origin "$TAG"
ok "Tag pushed — release workflow triggered"

# ── Summary ─────────────────────────────────────────────────────────
printf "\n${BOLD}${GREEN}Release ${VERSION} complete!${RESET}\n"
printf "  Commit:   ${SHORT_SHA}\n"
printf "  Tag:      ${TAG}\n"
printf "  Actions:  https://github.com/dbulnes/indigo-stats/actions\n"
printf "  Package:  https://github.com/dbulnes/indigo-stats/pkgs/container/indigo-stats\n"
