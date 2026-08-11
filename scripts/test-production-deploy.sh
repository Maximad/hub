#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

bash -n "$repo/scripts/deploy-production.sh"

# Execute the deployment phase directly from the production script with a
# Compose stub. This keeps the regression test independent of Docker, a deploy
# user, production paths, and network access while exercising the real command
# ordering and errexit behavior.
deployment_phase="$tmp/deployment-phase.sh"
awk '
    /^log "Building web image"$/ { copy = 1 }
    /^log "Checking recent logs"$/ { copy = 0 }
    copy
' "$repo/scripts/deploy-production.sh" >"$deployment_phase"

run_phase() (
    set -Eeuo pipefail
    events="$1"
    fail_migration="${2:-false}"

    log() { printf 'LOG %s\n' "$*" >>"$events"; }
    check_route() { printf 'ROUTE %s %s\n' "$1" "$2" >>"$events"; }
    dc() {
        printf 'DC' >>"$events"
        printf ' <%s>' "$@" >>"$events"
        printf '\n' >>"$events"
        if [[ "$fail_migration" == true && "${1:-}" == run &&
              "${6:-}" == manage.py && "${7:-}" == migrate ]]; then
            exit 42
        fi
    }

    # launch_readiness deliberately tolerates failure in production, but this
    # stub succeeds so only the migration failure path controls these fixtures.
    source "$deployment_phase"
)

events="$tmp/success.events"
run_phase "$events"

line_of() {
    local pattern="$1"
    local file="$2"
    local line
    line="$(grep -n -m1 -F "$pattern" "$file" | cut -d: -f1)"
    [[ -n "$line" ]] || { printf 'Missing event: %s\n' "$pattern" >&2; return 1; }
    printf '%s\n' "$line"
}

build_line="$(line_of 'DC <build> <web>' "$events")"
check_line="$(line_of 'DC <run> <--rm> <-T> <web> <python> <manage.py> <check>' "$events")"
migration_line="$(line_of 'DC <run> <--rm> <-T> <web> <python> <manage.py> <migrate> <--noinput>' "$events")"
replacement_line="$(line_of 'DC <up> <-d> <--no-deps> <--force-recreate> <web>' "$events")"
static_line="$(line_of 'DC <exec> <-T> <web> <python> <manage.py> <collectstatic> <--noinput> <--clear>' "$events")"
restart_line="$(line_of 'DC <restart> <web>' "$events")"
route_line="$(line_of 'ROUTE /menu/ 200' "$events")"

(( build_line < check_line ))
(( check_line < migration_line ))
(( migration_line < replacement_line ))
(( replacement_line < static_line ))
(( static_line < restart_line ))
(( restart_line < route_line ))

failed_events="$tmp/failure.events"
set +e
run_phase "$failed_events" true
failure_status=$?
set -e
if (( failure_status == 0 )); then
    echo "migration failure unexpectedly allowed deployment to continue" >&2
    exit 1
fi
grep -Fq 'DC <run> <--rm> <-T> <web> <python> <manage.py> <migrate> <--noinput>' "$failed_events"
if grep -Fq 'DC <up> <-d> <--no-deps> <--force-recreate> <web>' "$failed_events"; then
    echo "web replacement ran after a failed migration" >&2
    exit 1
fi

echo "production deployment ordering tests passed"
