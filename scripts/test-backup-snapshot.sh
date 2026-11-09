#!/usr/bin/env bash
# Dedicated disposable PostgreSQL container; never connects to the application DB.
set -Eeuo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
container="hub-backup-snapshot-${RANDOM}-$$"
real_docker="$(command -v docker)"
cleanup() {
    "$real_docker" rm -f "$container" >/dev/null 2>&1 || true
    rm -rf -- "$tmp"
}
trap cleanup EXIT
"$real_docker" run -d --name "$container" -e POSTGRES_USER=hub -e POSTGRES_DB=hub -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16-alpine >/dev/null
for attempt in {1..60}; do
    if "$real_docker" exec "$container" psql -h 127.0.0.1 -U hub -d hub -Atc 'SELECT 1' >/dev/null 2>&1; then break; fi
    sleep 1
done
"$real_docker" exec "$container" psql -U hub -d hub -v ON_ERROR_STOP=1 -c 'CREATE TABLE snapshot_probe (id integer); INSERT INTO snapshot_probe VALUES (1);' >/dev/null
mkdir -p "$tmp/bin" "$tmp/project/media"
git -C "$tmp/project" init -q
git -C "$tmp/project" -c user.name=CI -c user.email=ci@example.invalid commit --allow-empty -qm fixture
touch "$tmp/project/.env" "$tmp/project/docker-compose.prod.yml"
export SNAPSHOT_TEST_DOCKER="$real_docker" SNAPSHOT_TEST_CONTAINER="$container"
cat > "$tmp/bin/docker" <<'WRAPPER'
#!/usr/bin/env bash
set -Eeuo pipefail
shift 5 # compose -f file --env-file file
case "$1" in
    pause|unpause) exit 0 ;;
    exec)
        shift 3 # exec -T db
        if [[ "$1" == pg_dump ]]; then
            # A worker commits after snapshot export and before dump/count capture.
            "$SNAPSHOT_TEST_DOCKER" exec "$SNAPSHOT_TEST_CONTAINER" psql -U hub -d hub -v ON_ERROR_STOP=1 -c 'INSERT INTO snapshot_probe VALUES (2);' >/dev/null
        fi
        exec "$SNAPSHOT_TEST_DOCKER" exec -i "$SNAPSHOT_TEST_CONTAINER" "$@"
        ;;
    *) exit 2 ;;
esac
WRAPPER
chmod +x "$tmp/bin/docker"
PATH="$tmp/bin:$PATH" PROJECT_DIR="$tmp/project" BACKUP_ROOT="$tmp/backups" LOCK_FILE="$tmp/lock" \
    timeout 60 bash "$repo/scripts/backup-production.sh"
backup="$(find "$tmp/backups" -mindepth 1 -maxdepth 1 -type d -name 'hub-*')"
[[ -f "$backup/SUCCESS" ]]
grep -qx $'public.snapshot_probe\t1' "$backup/counts.tsv"
[[ "$("$real_docker" exec "$container" psql -U hub -d hub -Atc 'SELECT count(*) FROM snapshot_probe')" == 2 ]]
"$real_docker" exec "$container" createdb -U hub restored
"$real_docker" exec -i "$container" psql -U hub -d restored -v ON_ERROR_STOP=1 < "$backup/database.sql" >/dev/null
[[ "$("$real_docker" exec "$container" psql -U hub -d restored -Atc 'SELECT count(*) FROM snapshot_probe')" == 1 ]]
echo 'Backup dump and counts share the same snapshot despite concurrent writes.'
