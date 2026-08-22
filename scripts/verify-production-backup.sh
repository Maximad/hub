#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

backup="${1:-}"
[[ -n "$backup" && $# -eq 1 ]] || { echo "Usage: $0 /absolute/path/to/backup" >&2; exit 2; }
[[ "$backup" == /* && -d "$backup" && -f "$backup/SUCCESS" ]] || { echo "Not a successful absolute backup directory" >&2; exit 2; }
for file in database.sql media.tar.gz counts.tsv manifest.txt SHA256SUMS; do
    [[ -f "$backup/$file" ]] || { echo "Missing artifact: $file" >&2; exit 1; }
done

(cd "$backup" && sha256sum --check --strict SHA256SUMS)
tar --list --gzip --file "$backup/media.tar.gz" >/dev/null

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
container="hub-restore-verify-$$-$(date +%s)"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

docker run -d --name "$container" -e POSTGRES_PASSWORD=verify-only -e POSTGRES_DB=hub_restore postgres:16 >/dev/null

# The official Postgres image starts a temporary initialization server before
# shutting it down and exec'ing the final server. pg_isready can briefly
# succeed against that temporary server, so first wait for initialization to
# complete and only then wait for the final database to accept SQL queries.
init_complete=false
for _ in {1..60}; do
    if docker logs "$container" 2>&1 | grep -q 'PostgreSQL init process complete; ready for start up'; then
        init_complete=true
        break
    fi
    if [[ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)" != "true" ]]; then
        echo "Restore verification Postgres container exited during initialization" >&2
        docker logs "$container" >&2 || true
        exit 1
    fi
    sleep 1
done
[[ "$init_complete" == true ]] || {
    echo "Restore verification Postgres initialization timed out" >&2
    docker logs "$container" >&2 || true
    exit 1
}

ready=false
for _ in {1..30}; do
    if docker exec "$container" pg_isready -U postgres -d hub_restore >/dev/null 2>&1 && \
       docker exec "$container" psql -X -U postgres -d hub_restore -v ON_ERROR_STOP=1 -c 'SELECT 1' >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
[[ "$ready" == true ]] || {
    echo "Restore verification Postgres did not become stably ready" >&2
    docker logs "$container" >&2 || true
    exit 1
}

docker exec -i "$container" psql -X -U postgres -d hub_restore -v ON_ERROR_STOP=1 <"$backup/database.sql" >/dev/null

actual="$(mktemp)"
trap 'rm -f "${actual:-}"; cleanup' EXIT INT TERM
docker exec "$container" psql -X -U postgres -d hub_restore -A -t -v ON_ERROR_STOP=1 -F $'\t' -c \
    "SELECT schemaname||'.'||relname, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', schemaname, relname), false, true, '')))[1]::text::bigint FROM pg_stat_user_tables ORDER BY 1" \
    >"$actual"

cmp "$backup/counts.tsv" "$actual" || { echo "Restored essential table counts differ from backup" >&2; exit 1; }
date -u +%Y-%m-%dT%H:%M:%SZ >"$backup/RESTORE_VERIFIED"
echo "Restore verification succeeded: $backup"
