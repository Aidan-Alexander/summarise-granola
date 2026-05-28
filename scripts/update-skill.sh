#!/bin/bash
# Update this skill from its public GitHub repo.
# Clones the latest version and rsyncs it over, preserving local data/.
#
# Usage: bash scripts/update-skill.sh
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_NAME="$(basename "$SKILL_DIR")"
REPO="HartreeWorks/skill--$SKILL_NAME"
TEMP_DIR=""

cleanup() {
  if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
    rm -rf "$TEMP_DIR"
  fi
}
trap cleanup EXIT

echo "Updating ${SKILL_NAME} from https://github.com/${REPO}..."

TEMP_DIR="$(mktemp -d)"
git clone --depth 1 "https://github.com/${REPO}.git" "$TEMP_DIR/repo" 2>&1 | sed 's/^/  /'

rsync -av --delete \
  --exclude='data/' \
  --exclude='.git' \
  --exclude='.update-cache' \
  --exclude='.update-check-disabled' \
  --exclude='.DS_Store' \
  "$TEMP_DIR/repo/" "$SKILL_DIR/" 2>&1 | sed 's/^/  /'

echo ""
echo "Updated ${SKILL_NAME} to $(tr -d '[:space:]' < "$SKILL_DIR/VERSION")."
