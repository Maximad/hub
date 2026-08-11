#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

bash -n "$repo/scripts/backup-production.sh" "$repo/scripts/verify-production-backup.sh"

# A corrupt artifact must fail before verification attempts to start Docker.
fixture="$tmp/hub-20260101T000000Z"
mkdir "$fixture"
printf '%s\n' '-- PostgreSQL database dump complete' >"$fixture/database.sql"
tar -czf "$fixture/media.tar.gz" -C "$tmp" --files-from /dev/null
: >"$fixture/counts.tsv"
printf '%s\n' 'format_version=1' >"$fixture/manifest.txt"
(cd "$fixture" && sha256sum database.sql media.tar.gz counts.tsv manifest.txt >SHA256SUMS)
touch "$fixture/SUCCESS"
printf '%s\n' corrupt >>"$fixture/database.sql"
if "$repo/scripts/verify-production-backup.sh" "$fixture" >"$tmp/output" 2>&1; then
    echo "corrupt backup was unexpectedly accepted" >&2
    exit 1
fi
grep -q 'database.sql: FAILED' "$tmp/output"

if BACKUP_ROOT=/ PROJECT_DIR="$tmp" "$repo/scripts/backup-production.sh" >"$tmp/root-output" 2>&1; then
    echo "unsafe backup root was unexpectedly accepted" >&2
    exit 1
fi
grep -q 'BACKUP_ROOT must be a non-root absolute path' "$tmp/root-output"
echo "backup failure-path tests passed"
