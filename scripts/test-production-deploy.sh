#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

bash -n "$repo/scripts/deploy-production.sh"

# Keep the MikroTik production overlay mandatory when the feature is enabled,
# and ensure both long-running application services use the same Compose stack.
grep -Fq 'MIKROTIK_COMPOSE_FILE="docker-compose.mikrotik.yml"' "$repo/scripts/deploy-production.sh"
grep -Fq 'COMPOSE_ARGS+=(-f "$MIKROTIK_COMPOSE_FILE")' "$repo/scripts/deploy-production.sh"
grep -Fq 'MIKROTIK_ENABLED=true requires' "$repo/scripts/deploy-production.sh"
grep -Fq 'dc build web internet-worker' "$repo/scripts/deploy-production.sh"
grep -Fq 'dc up -d --no-deps --force-recreate web internet-worker' "$repo/scripts/deploy-production.sh"

# Execute the deployment phase directly from the production script with a
# Compose stub. This keeps the regression test independent of Docker, a deploy
# user, production paths, and network access while exercising the real command
# ordering and errexit behavior.
deployment_phase="$tmp/deployment-phase.sh"
awk '
    /^log "Building application images"$/ { copy = 1 }
    /^log "Checking recent logs"$/ { copy = 0 }
    copy
' "$repo/scripts/deploy-production.sh" >"$deployment_phase"

run_phase() (
    set -Eeuo pipefail
    events="$1"
    fail_migration="${2:-false}"
    fail_readiness="${3:-false}"
    PROJECT_DIR="/opt/hub"

    log() { printf 'LOG %s\n' "$*" >>"$events"; }
    check_route() { printf 'ROUTE %s %s\n' "$1" "$2" >>"$events"; }
    mikrotik_enabled() { return 0; }
    dc() {
        printf 'DC' >>"$events"
        printf ' <%s>' "$@" >>"$events"
        printf '\n' >>"$events"

        local i next
        for ((i=1; i<$#; i++)); do
            next=$((i + 1))
            if [[ "${!i}" == manage.py ]]; then
                if [[ "$fail_migration" == true && "${!next}" == migrate ]]; then
                    exit 42
                fi
                if [[ "$fail_readiness" == true && "${!next}" == launch_readiness ]]; then
                    exit 43
                fi
            fi
        done
    }
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

build_line="$(line_of 'DC <build> <web> <internet-worker>' "$events")"
check_line="$(line_of 'DC <run> <--rm> <-T> <web> <python> <manage.py> <check>' "$events")"
migration_line="$(line_of 'DC <run> <--rm> <-T> <web> <python> <manage.py> <migrate> <--noinput>' "$events")"
readiness_line="$(line_of '<manage.py> <launch_readiness> <--json>' "$events")"
replacement_line="$(line_of 'DC <up> <-d> <--no-deps> <--force-recreate> <web> <internet-worker>' "$events")"
static_line="$(line_of 'DC <exec> <-T> <web> <python> <manage.py> <collectstatic> <--noinput> <--clear>' "$events")"
restart_line="$(line_of 'DC <restart> <web>' "$events")"
route_line="$(line_of 'ROUTE /menu/ 200' "$events")"
mikrotik_line="$(line_of 'DC <exec> <-T> <web> <python> <manage.py> <mikrotik_healthcheck> <--json>' "$events")"
runtime_readiness_line="$(line_of 'DC <exec> <-T> <web> <python> <manage.py> <internet_readiness> <--json>' "$events")"

(( build_line < check_line ))
(( check_line < migration_line ))
(( migration_line < readiness_line ))
(( readiness_line < replacement_line ))
(( replacement_line < static_line ))
(( static_line < restart_line ))
(( restart_line < route_line ))
(( route_line < mikrotik_line ))
(( mikrotik_line < runtime_readiness_line ))

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
if grep -Fq 'DC <up> <-d> <--no-deps> <--force-recreate> <web> <internet-worker>' "$failed_events"; then
    echo "application replacement ran after a failed migration" >&2
    exit 1
fi

readiness_failed_events="$tmp/readiness-failure.events"
set +e
run_phase "$readiness_failed_events" false true
readiness_failure_status=$?
set -e
if (( readiness_failure_status == 0 )); then
    echo "launch readiness FAIL unexpectedly allowed deployment to continue" >&2
    exit 1
fi
grep -Fq '<manage.py> <launch_readiness> <--json>' "$readiness_failed_events"
if grep -Fq 'DC <up> <-d> <--no-deps> <--force-recreate> <web> <internet-worker>' "$readiness_failed_events"; then
    echo "application replacement ran after a failed launch readiness gate" >&2
    exit 1
fi

# The revision marker is after the gated phase, so errexit cannot write it when
# launch readiness returns FAIL.
readiness_source_line="$(grep -n -m1 'manage.py launch_readiness --json' "$repo/scripts/deploy-production.sh" | cut -d: -f1)"
marker_source_line="$(grep -n -m1 'last_deployed_revision.txt.tmp' "$repo/scripts/deploy-production.sh" | cut -d: -f1)"
(( readiness_source_line < marker_source_line ))
if grep -F 'manage.py launch_readiness' "$repo/scripts/deploy-production.sh" | grep -Fq '||'; then
    echo "launch readiness exit status is still suppressed" >&2
    exit 1
fi

echo "production deployment ordering tests passed"
