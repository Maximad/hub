#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

EXPECTED_USER="deploy"
PROJECT_DIR="/opt/hub"
BRANCH="main"
COMPOSE_FILE="docker-compose.prod.yml"
MIKROTIK_COMPOSE_FILE="docker-compose.mikrotik.yml"
ENV_FILE=".env"
BASE_URL="https://hubsweida.jwtalenthouse.com"
LOCK_FILE="/tmp/hub-production-operation.lock"
COMPOSE_ARGS=(-f "$COMPOSE_FILE")

BACKUP_FILE=""
BOOTSTRAP_RESUME=false
if [[ "${1:-}" == "--bootstrap-resume" ]]; then
    [[ $# -eq 4 ]] || { echo "Invalid bootstrap handoff" >&2; exit 2; }
    BOOTSTRAP_RESUME=true
    BOOTSTRAP_ROLLBACK="$2"
    BOOTSTRAP_TARGET="$3"
    BOOTSTRAP_BACKUP="$4"
elif [[ $# -ne 0 ]]; then
    echo "Usage: $0 [--bootstrap-resume ROLLBACK TARGET VERIFIED_BACKUP]" >&2
    exit 2
fi

log() {
    printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

die() {
    printf '\nDEPLOYMENT STOPPED: %s\n' "$*" >&2
    exit 1
}

mikrotik_enabled() {
    grep -Eiq \
        '^[[:space:]]*MIKROTIK_ENABLED[[:space:]]*=[[:space:]]*(true|1|yes|on)[[:space:]]*$' \
        "$ENV_FILE"
}

dc() {
    docker compose \
        "${COMPOSE_ARGS[@]}" \
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
    dc logs --tail=150 web internet-worker >&2 || true

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

if [[ -f "$MIKROTIK_COMPOSE_FILE" ]]; then
    COMPOSE_ARGS+=(-f "$MIKROTIK_COMPOSE_FILE")
elif mikrotik_enabled; then
    die "MIKROTIK_ENABLED=true requires $PROJECT_DIR/$MIKROTIK_COMPOSE_FILE."
fi

[[ -d .git ]] ||
    die "$PROJECT_DIR is not a Git repository."

command -v git >/dev/null || die "git is missing."
command -v docker >/dev/null || die "docker is missing."
command -v curl >/dev/null || die "curl is missing."
command -v flock >/dev/null || die "flock is missing."

docker info >/dev/null 2>&1 ||
    die "Docker is unavailable to the deploy user."

dc config --quiet ||
    die "Combined production Docker Compose configuration is invalid."

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

if [[ "$BOOTSTRAP_RESUME" == true ]]; then
    log "Validating bootstrap handoff"
    [[ "$CURRENT_COMMIT" == "$BOOTSTRAP_TARGET" ]] || die "Bootstrap target does not match HEAD."
    [[ "$(git rev-parse --verify origin/$BRANCH^{commit})" == "$BOOTSTRAP_TARGET" ]] || die "origin/main changed after bootstrap."
    [[ "$(cat backups/rollback_revision.txt)" == "$BOOTSTRAP_ROLLBACK" ]] || die "Rollback revision record is inaccurate."
    git merge-base --is-ancestor "$BOOTSTRAP_ROLLBACK" "$BOOTSTRAP_TARGET" || die "Invalid bootstrap revision range."
    [[ "$BOOTSTRAP_BACKUP" == "$PROJECT_DIR"/backups/production/* && -f "$BOOTSTRAP_BACKUP/SUCCESS" ]] || die "Invalid bootstrap backup."
    ./scripts/verify-production-backup.sh "$BOOTSTRAP_BACKUP"
    TARGET_COMMIT="$CURRENT_COMMIT"
    BACKUP_FILE="${BOOTSTRAP_BACKUP#$PROJECT_DIR/}"
else
    log "Fetching origin/main"

    git fetch origin "$BRANCH"

    TARGET_COMMIT="$(git rev-parse origin/$BRANCH)"

    git merge-base --is-ancestor "$CURRENT_COMMIT" "$TARGET_COMMIT" ||
        die "Local main cannot be safely fast-forwarded."

    printf 'Current commit: %s\n' "$CURRENT_COMMIT"
    printf 'Target commit:  %s\n' "$TARGET_COMMIT"

    log "Creating full production backup"
    ./scripts/backup-production.sh --lock-held
    BACKUP_FILE="backups/production (latest successful backup)"

    log "Updating source code"

    printf '%s\n' "$CURRENT_COMMIT" >backups/rollback_revision.txt.tmp
    mv backups/rollback_revision.txt.tmp backups/rollback_revision.txt
    git merge --ff-only "origin/$BRANCH"
fi

log "Building application images"

dc build web internet-worker

log "Running pre-deployment checks"

dc run --rm -T web python manage.py check
dc run --rm -T web python manage.py makemigrations --check --dry-run

log "Applying migrations"

# Migrations run from the target image while the previous release continues to
# serve traffic. Every production migration must therefore be compatible with
# the currently running release; destructive changes require an
# expand/migrate/contract rollout across releases.
dc run --rm -T web python manage.py migrate --noinput

log "Evaluating launch readiness gate"

# Run the read-only audit from the target image before traffic is cut over.
# Mount only the production-backup evidence path, read-only, for this one-off
# check. The long-running web container does not need access to database dumps.
# Machine-readable mode still exits nonzero for FAIL, while WARN remains an
# approved, visible, non-blocking result. Do not suppress this exit status:
# errexit prevents both cutover and the deployed-revision write on failure.
dc run --rm -T \
    --volume "$PROJECT_DIR/backups/production:/opt/hub/backups/production:ro" \
    web python manage.py launch_readiness --json

log "Replacing web + Internet worker containers"

dc up -d --no-deps --force-recreate web internet-worker

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

if mikrotik_enabled; then
    log "Checking MikroTik connectivity"
    dc exec -T web python manage.py mikrotik_healthcheck --json
fi

dc exec -T web python manage.py internet_readiness --json

log "Checking routes"

check_route "/menu/" "200"
check_route "/admin/login/" "200"
check_route "/staff/" "302"
check_route "/staff/orders/" "302"
check_route "/staff/cashier/" "302"
check_route "/staff/pos/" "302"

log "Checking recent logs"

ERRORS="$(
    dc logs --since=5m web internet-worker 2>&1 |
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

printf '%s\n' "$DEPLOY_COMMIT" >backups/last_deployed_revision.txt.tmp
mv backups/last_deployed_revision.txt.tmp backups/last_deployed_revision.txt

log "Deployment completed successfully"

printf '\nDeployed commit: %s\n' "$DEPLOY_COMMIT"
printf 'Database backup: %s\n' "$BACKUP_FILE"
printf 'Public menu: %s/menu/\n' "$BASE_URL"
