#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

EXPECTED_USER="deploy"
PROJECT_DIR="/opt/hub"
BRANCH="main"
COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env"
BASE_URL="https://hubsweida.jwtalenthouse.com"
LOCK_FILE="/tmp/hub-production-deploy.lock"

TIMESTAMP="$(date +%F_%H-%M-%S)"
BACKUP_FILE=""

log() {
    printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

die() {
    printf '\nDEPLOYMENT STOPPED: %s\n' "$*" >&2
    exit 1
}

dc() {
    docker compose \
        -f "$COMPOSE_FILE" \
        --env-file "$ENV_FILE" \
        "$@"
}

on_error() {
    code=$?

    printf '\nDEPLOYMENT FAILED with exit code %s\n' "$code" >&2

    if [[ -n "$BACKUP_FILE" ]]; then
        printf 'Backup: %s/%s\n' "$PROJECT_DIR" "$BACKUP_FILE" >&2
    fi

    dc ps >&2 || true
    dc logs --tail=150 web >&2 || true

    exit "$code"
}

trap on_error ERR

check_route() {
    path="$1"
    expected="$2"
    attempts="${3:-15}"
    code=""

    for ((i=1; i<=attempts; i++)); do
        code="$(
            curl -k -sS \
                --connect-timeout 8 \
                --max-time 20 \
                -o /dev/null \
                -w '%{http_code}' \
                "${BASE_URL}${path}" || true
        )"

        if [[ "$code" == "$expected" ]]; then
            printf 'PASS  %-28s %s\n' "$path" "$code"
            return 0
        fi

        sleep 2
    done

    printf 'FAIL  %-28s expected=%s received=%s\n' \
        "$path" "$expected" "${code:-none}" >&2
    return 1
}

[[ "$(id -u)" != "0" ]] ||
    die "Refusing to run as root. Run this script as $EXPECTED_USER."

[[ "$(id -un)" == "$EXPECTED_USER" ]] ||
    die "Run this script as deploy, not $(id -un)."

cd "$PROJECT_DIR"

[[ "$(pwd)" == "$PROJECT_DIR" ]] ||
    die "Incorrect project directory."

[[ -f "$ENV_FILE" ]] ||
    die "Missing $PROJECT_DIR/$ENV_FILE."

[[ -f "$COMPOSE_FILE" ]] ||
    die "Missing $PROJECT_DIR/$COMPOSE_FILE."

[[ -d .git ]] ||
    die "$PROJECT_DIR is not a Git repository."

command -v git >/dev/null || die "git is missing."
command -v docker >/dev/null || die "docker is missing."
command -v curl >/dev/null || die "curl is missing."
command -v flock >/dev/null || die "flock is missing."

docker info >/dev/null 2>&1 ||
    die "Docker is unavailable to the deploy user."

exec 9>"$LOCK_FILE"
flock -n 9 ||
    die "Another deployment is already running."

log "Checking repository"

[[ "$(git branch --show-current)" == "$BRANCH" ]] ||
    die "The repository must be on branch main."

if [[ -n "$(git status --porcelain)" ]]; then
    git status --short
    die "The repository contains local changes."
fi

CURRENT_COMMIT="$(git rev-parse HEAD)"

log "Fetching origin/main"

git fetch origin "$BRANCH"

TARGET_COMMIT="$(git rev-parse origin/$BRANCH)"

git merge-base --is-ancestor "$CURRENT_COMMIT" "$TARGET_COMMIT" ||
    die "Local main cannot be safely fast-forwarded."

printf 'Current commit: %s\n' "$CURRENT_COMMIT"
printf 'Target commit:  %s\n' "$TARGET_COMMIT"

log "Creating database backup"

mkdir -p backups

BACKUP_FILE="backups/hub_${TIMESTAMP}_before_deploy.sql"

dc exec -T db pg_dump -U hub -d hub > "$BACKUP_FILE"

[[ -s "$BACKUP_FILE" ]] ||
    die "Database backup is empty."

BACKUP_SIZE="$(wc -c < "$BACKUP_FILE")"

(( BACKUP_SIZE >= 1000 )) ||
    die "Database backup is unexpectedly small."

grep -q "PostgreSQL database dump complete" "$BACKUP_FILE" ||
    die "Database backup did not complete successfully."

printf 'Backup: %s\n' "$BACKUP_FILE"
printf 'Size:   %s bytes\n' "$BACKUP_SIZE"

log "Updating source code"

git merge --ff-only "origin/$BRANCH"

log "Building web image"

dc build web

log "Running pre-deployment checks"

dc run --rm -T web python manage.py check
dc run --rm -T web python manage.py makemigrations --check --dry-run

log "Replacing web container"

dc up -d --no-deps --force-recreate web

log "Applying migrations"

dc exec -T web python manage.py migrate --noinput

log "Collecting static files"

dc exec -T web mkdir -p /app/staticfiles
dc exec -T web python manage.py collectstatic --noinput --clear

log "Restarting web service"

dc restart web

log "Waiting for application"

check_route "/menu/" "200" 30

log "Running production checks"

dc exec -T web python manage.py check
dc exec -T web python manage.py smoke_check
dc exec -T web python manage.py system_audit

log "Checking routes"

check_route "/menu/" "200"
check_route "/admin/login/" "200"
check_route "/staff/" "302"
check_route "/staff/orders/" "302"
check_route "/staff/cashier/" "302"
check_route "/staff/pos/" "302"

log "Checking recent logs"

ERRORS="$(
    dc logs --since=5m web 2>&1 |
        grep -Ei \
        "traceback|server error|invalidstorageerror|noreversematch|templatedoesnotexist|programmingerror|operationalerror|modulenotfounderror" \
        || true
)"

if [[ -n "$ERRORS" ]]; then
    printf '\nWARNING: possible errors found:\n%s\n' "$ERRORS"
else
    printf 'No critical error patterns found.\n'
fi

DEPLOY_COMMIT="$(git rev-parse HEAD)"

printf '%s\n' "$DEPLOY_COMMIT" > backups/last_deployed_revision.txt

log "Deployment completed successfully"

printf '\nDeployed commit: %s\n' "$DEPLOY_COMMIT"
printf 'Database backup: %s\n' "$BACKUP_FILE"
printf 'Public menu: %s/menu/\n' "$BASE_URL"
