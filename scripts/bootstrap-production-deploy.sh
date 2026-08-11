#!/usr/bin/env bash

# One-time bridge for deployments whose checked-out backup retention code cannot
# complete deploy-production.sh.  This file is intentionally run from /tmp via
# `git show`; see docs/production-backups.md.
set -Eeuo pipefail
IFS=$'\n\t'

EXPECTED_USER="${EXPECTED_USER:-deploy}"
PROJECT_DIR="${PROJECT_DIR:-/opt/hub}"
BRANCH="main"
LOCK_FILE="${LOCK_FILE:-/tmp/hub-production-operation.lock}"

die() { printf '\nBOOTSTRAP STOPPED: %s\n' "$*" >&2; exit 1; }
log() { printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

[[ "$(id -u)" != 0 ]] || die "Refusing to run as root. Run this script as $EXPECTED_USER."
[[ "$(id -un)" == "$EXPECTED_USER" ]] || die "Run this script as $EXPECTED_USER, not $(id -un)."
cd "$PROJECT_DIR"
[[ "$(pwd)" == "$PROJECT_DIR" && -d .git ]] || die "Invalid project directory."
[[ "$(git branch --show-current)" == "$BRANCH" ]] || die "The repository must be on branch main."
[[ -z "$(git status --porcelain)" ]] || die "The repository contains local changes."

exec 9>"$LOCK_FILE"
flock -n 9 || die "Another production operation is already running."

rollback_revision="$(git rev-parse HEAD)"

log "Finding and verifying the latest successful full backup"
latest="$({ find "$PROJECT_DIR/backups/production" -mindepth 1 -maxdepth 1 -type d \
    -name 'hub-*' -exec test -f '{}/SUCCESS' \; -print || true; } | sort | tail -1)"
[[ -n "$latest" ]] || die "No successful full production backup exists."
"$PROJECT_DIR/scripts/verify-production-backup.sh" "$latest"

log "Fetching and validating origin/main"
git fetch origin "$BRANCH"
target_revision="$(git rev-parse --verify "origin/$BRANCH^{commit}")"
git merge-base --is-ancestor "$rollback_revision" "$target_revision" ||
    die "origin/main is not a fast-forward of the checked-out revision."
git diff --quiet && git diff --cached --quiet || die "The repository changed during verification."
[[ "$(git rev-parse HEAD)" == "$rollback_revision" ]] || die "HEAD changed during verification."

printf 'Rollback revision: %s\nTarget revision:   %s\nBackup verified:   %s\n' \
    "$rollback_revision" "$target_revision" "$latest"

log "Recording rollback revision and moving source (containers are not touched)"
mkdir -p backups
printf '%s\n' "$rollback_revision" >backups/rollback_revision.txt.tmp
mv backups/rollback_revision.txt.tmp backups/rollback_revision.txt
git merge --ff-only "origin/$BRANCH"
[[ "$(git rev-parse HEAD)" == "$target_revision" ]] || die "Checkout did not reach validated origin/main."

log "Handing off to the corrected deployment script"
exec ./scripts/deploy-production.sh --bootstrap-resume \
    "$rollback_revision" "$target_revision" "$latest"
