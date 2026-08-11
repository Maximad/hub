#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

bash -n "$repo/scripts/backup-production.sh" \
    "$repo/scripts/production-backup-retention.sh" \
    "$repo/scripts/verify-production-backup.sh"

source "$repo/scripts/production-backup-retention.sh"

# Retention accepts only exact, real UTC timestamps and ignores other entries.
retention="$tmp/retention"
outside="$tmp/outside"
mkdir -p "$retention" "$outside"
valid=(
    hub-20260809T235959Z
    hub-20260810T040000Z
    hub-20260810T070000Z
    hub-20260811T053021Z
)
for name in "${valid[@]}"; do
    mkdir "$retention/$name"
    touch "$retention/$name/SUCCESS"
done
malformed=(
    hub-20260811T053021
    hub-20260811-053021Z
    hub-20260811T05302Z
    hub-20260230T053021Z
    hub-20260811T256061Z
)
for name in "${malformed[@]}"; do
    mkdir "$retention/$name"
    touch "$retention/$name/SUCCESS"
done
touch "$retention/unrelated.txt" "$outside/must-not-be-removed"
ln -s "$outside" "$retention/hub-20250811T053021Z"

[[ "$(production_backup_timestamp_epoch hub-20260811T053021Z)" == 1786426221 ]]
if production_backup_timestamp_epoch hub-20260230T053021Z >/dev/null; then
    echo "impossible backup timestamp was unexpectedly accepted" >&2
    exit 1
fi

# Pin the clock so ordering and the one-day cutoff remain deterministic.
date() {
    if [[ "$*" == "-u -d 1 days ago +%s" ]]; then
        printf '%s\n' 1786341600 # 2026-08-10T06:00:00Z
    else
        command date "$@"
    fi
}
cleanup_production_backups "$retention" 1
unset -f date

[[ ! -e "$retention/hub-20260809T235959Z" ]]
[[ ! -e "$retention/hub-20260810T040000Z" ]]
[[ -d "$retention/hub-20260810T070000Z" ]]
[[ -d "$retention/hub-20260811T053021Z" ]]
for name in "${malformed[@]}"; do
    [[ -d "$retention/$name" ]]
done
[[ -f "$retention/unrelated.txt" && -f "$outside/must-not-be-removed" ]]
[[ -L "$retention/hub-20250811T053021Z" ]]

# A newly successful backup must return cleanly from retention, as deployment expects.
single="$tmp/single"
mkdir -p "$single/hub-20260811T053021Z"
touch "$single/hub-20260811T053021Z/SUCCESS"
cleanup_production_backups "$single" 14

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
