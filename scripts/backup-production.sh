#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

PROJECT_DIR="${PROJECT_DIR:-/opt/hub}"
BACKUP_ROOT="${BACKUP_ROOT:-$PROJECT_DIR/backups/production}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
LOCK_FILE="${LOCK_FILE:-/tmp/hub-production-operation.lock}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env}"
LOCK_HELD=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

source "$SCRIPT_DIR/production-backup-retention.sh"

die() { printf 'BACKUP FAILED: %s\n' "$*" >&2; exit 1; }
log() { printf '[%s] %s\n' "$(date -u '+%FT%TZ')" "$*"; }

if [[ "${1:-}" == "--lock-held" ]]; then
    LOCK_HELD=true
    shift
fi
[[ $# -eq 0 ]] || die "Usage: $0 [--lock-held]"
[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || die "RETENTION_DAYS must be a non-negative integer"
[[ "$BACKUP_ROOT" == /* && "$BACKUP_ROOT" != "/" ]] || die "BACKUP_ROOT must be a non-root absolute path"

cd "$PROJECT_DIR" || die "Cannot enter $PROJECT_DIR"
for command in docker git flock sha256sum tar; do
    command -v "$command" >/dev/null || die "$command is required"
done
[[ -f "$COMPOSE_FILE" && -f "$ENV_FILE" ]] || die "Production Compose or environment file is missing"

if [[ "$LOCK_HELD" != true ]]; then
    exec 9>"$LOCK_FILE"
    flock -n 9 || die "A production backup or deployment is already running"
fi

dc() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="hub-$timestamp"
mkdir -p "$BACKUP_ROOT"
root_real="$(cd "$BACKUP_ROOT" && pwd -P)"
[[ "$root_real" == "$BACKUP_ROOT" || "$BACKUP_ROOT" == "$root_real" ]] || die "BACKUP_ROOT must not contain symlinks"
staging="$BACKUP_ROOT/.$name.incomplete"
final="$BACKUP_ROOT/$name"
[[ ! -e "$staging" && ! -e "$final" ]] || die "Backup name already exists"
mkdir -m 0700 "$staging"
web_paused=false
cleanup() {
    if [[ "$web_paused" == true ]]; then
        dc unpause web >/dev/null 2>&1 || true
    fi
    if [[ -n "${staging:-}" && -d "$staging" ]]; then
        rm -rf -- "$staging"
    fi
    return 0
}
trap cleanup EXIT

log "Pausing application writes for a consistent snapshot"
dc pause web >/dev/null
web_paused=true

log "Dumping PostgreSQL"
dc exec -T db pg_dump --username "${POSTGRES_USER:-hub}" --dbname "${POSTGRES_DB:-hub}" \
    --no-owner --no-privileges >"$staging/database.sql"
[[ -s "$staging/database.sql" ]] || die "Database dump is empty"
grep -q '^-- PostgreSQL database dump complete' "$staging/database.sql" || die "Database dump is incomplete"

# Each row is table name<TAB>row count. Identifiers are quoted by PostgreSQL itself.
dc exec -T db psql --username "${POSTGRES_USER:-hub}" --dbname "${POSTGRES_DB:-hub}" \
    -X -A -t -v ON_ERROR_STOP=1 -F $'\t' -c \
    "SELECT schemaname||'.'||relname, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', schemaname, relname), false, true, '')))[1]::text::bigint FROM pg_stat_user_tables ORDER BY 1" \
    >"$staging/counts.tsv"

log "Archiving media"
mkdir -p media
tar --create --gzip --file "$staging/media.tar.gz" --directory "$PROJECT_DIR" media
tar --list --gzip --file "$staging/media.tar.gz" >/dev/null
dc unpause web >/dev/null
web_paused=false

db_bytes="$(wc -c <"$staging/database.sql" | tr -d ' ')"
media_bytes="$(wc -c <"$staging/media.tar.gz" | tr -d ' ')"
table_count="$(wc -l <"$staging/counts.tsv" | tr -d ' ')"
database_rows="$(awk -F '\t' '{ total += $2 } END { print total + 0 }' "$staging/counts.tsv")"
media_files="$(find media -type f -printf . | wc -c | tr -d ' ')"
commit="$(git rev-parse HEAD)"
cat >"$staging/manifest.txt" <<EOF
format_version=1
created_utc=$timestamp
git_commit=$commit
database_bytes=$db_bytes
media_archive_bytes=$media_bytes
database_tables=$table_count
database_rows=$database_rows
media_files=$media_files
EOF

(cd "$staging" && sha256sum database.sql media.tar.gz counts.tsv manifest.txt >SHA256SUMS)
(cd "$staging" && sha256sum --check --strict SHA256SUMS)
tar --list --gzip --file "$staging/media.tar.gz" >/dev/null
[[ "$(wc -l <"$staging/manifest.txt")" -eq 8 ]] || die "Manifest validation failed"

touch "$staging/SUCCESS"
mv -- "$staging" "$final"
staging=""
log "Backup completed: $final"

# Only inspect direct, validated backup directories, and always preserve the newest success.
cleanup_production_backups "$root_real" "$RETENTION_DAYS"
