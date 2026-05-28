#!/bin/bash
# Check if a newer version of this skill is available on GitHub.
# Derives the repo from the convention: HartreeWorks/skill--{skill-name}
#
# Caches results for 24 hours. Prints a one-liner if an update is available,
# silent otherwise. Fails gracefully on network issues (3s timeout).
#
# Usage:
#   bash scripts/check-update.sh            # check for updates
#   bash scripts/check-update.sh --snooze   # suppress for 24h
#   bash scripts/check-update.sh --disable  # never check again
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_NAME="$(basename "$SKILL_DIR")"
VERSION_FILE="$SKILL_DIR/VERSION"
CACHE_FILE="$SKILL_DIR/.update-cache"
DISABLE_FILE="$SKILL_DIR/.update-check-disabled"
REPO="HartreeWorks/skill--$SKILL_NAME"
CHECK_INTERVAL=86400  # 24 hours

# Handle flags
case "${1:-}" in
  --snooze)
    echo -n "" > "$CACHE_FILE"
    echo "Update check snoozed for 24 hours."
    exit 0
    ;;
  --disable)
    touch "$DISABLE_FILE"
    echo "Update checks disabled. Remove $DISABLE_FILE to re-enable."
    exit 0
    ;;
esac

# Skip if permanently disabled
if [ -f "$DISABLE_FILE" ]; then
  exit 0
fi

# Read local version
if [ ! -f "$VERSION_FILE" ]; then
  exit 0
fi
LOCAL_VERSION=$(tr -d '[:space:]' < "$VERSION_FILE")

# Check cache
if [ -f "$CACHE_FILE" ]; then
  last_check=$(stat -f %m "$CACHE_FILE" 2>/dev/null || stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0)
  now=$(date +%s)
  if (( now - last_check < CHECK_INTERVAL )); then
    cached=$(cat "$CACHE_FILE")
    if [ -n "$cached" ]; then
      echo "$cached"
    fi
    exit 0
  fi
fi

# Fetch remote version (3s timeout, fail silently)
REMOTE_VERSION=$(curl -sf --max-time 3 \
  "https://raw.githubusercontent.com/${REPO}/main/VERSION" 2>/dev/null \
  | tr -d '[:space:]' || echo "")

# Compare and cache result (cache even on fetch failure to avoid unnecessary requests)
if [ -n "$REMOTE_VERSION" ] && [ "$REMOTE_VERSION" != "$LOCAL_VERSION" ]; then
  MSG="[skill-update] ${SKILL_NAME}: update available (${LOCAL_VERSION} → ${REMOTE_VERSION}). See https://github.com/${REPO}"
  echo "$MSG"
  echo "$MSG" > "$CACHE_FILE"
else
  # Up to date or fetch failed — cache so we don't recheck for 24h
  echo -n "" > "$CACHE_FILE"
fi
