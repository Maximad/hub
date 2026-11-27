#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

bash -n "$repo/scripts/deploy-production.sh"

# Keep the MikroTik production overlay mandatory when the feature is enabled,
# ensure all long-running application services use the same Compose stack,
# keep the MCP bridge in the deploy/recovery/logging path, and ensure a freshly
# created backup is restore-verified before readiness.
grep -Fq 'MIKROTIK_COMPOSE_FILE="docker-compose.mikrotik.yml"' "$repo/scripts/deploy-production.sh"
grep -Fq 'COMPOSE_ARGS+=(-f "$MIKROTIK_COMPOSE_FILE")' "$repo/scripts/deploy-production.sh"
grep -Fq 'MIKROTIK_ENABLED=true requires' "$repo/scripts/deploy-production.sh"
grep -Fq 'dc up -d --no-deps --no-build --force-recreate web management-mcp internet-worker notification-worker' "$repo/scripts/deploy-production.sh"
grep -Fq 'dc exec -T management-mcp python scripts/check-mcp-bridge.py' "$repo/scripts/deploy-production.sh"
grep -Fq 'dc logs --tail=150 web management-mcp internet-worker notification-worker' "$repo/scripts/deploy-production.sh"
grep -Fq 'dc logs --since=5m web management-mcp internet-worker notification-worker' "$repo/scripts/deploy-production.sh"
grep -Fq 'log "Verifying fresh production backup restore"' "$repo/scripts/deploy-production.sh"
grep -Fq './scripts/verify-production-backup.sh "$LATEST_BACKUP"' "$repo/scripts/deploy-production.sh"
grep -Fq 'ALLOW_PRELAUNCH_OPERATIONAL_DATA=' "$repo/scripts/deploy-production.sh"
grep -Fq 'launch_readiness --json --allow-operational-data' "$repo/scripts/deploy-production.sh"

# Execute the deployment phase directly from the production script with a
# Compose stub. This keeps the regression test independent of Docker, a deploy
# user, production paths, and network access while exercising the real command
# ordering and errexit behavior.
deployment_phase="$tmp/deployment-phase.sh"
awk '
    /^log "Running pre-deployment checks"$/ { copy = 1 }
    /^log "Checking recent logs"$/ { copy = 0 }
    copy
' "$repo/scripts/deploy-production.sh" >"$deployment_phase"

run_phase() (
    set -Eeuo pipefail
    events="$1"
    fail_migration="${2:-false}"
    fail_readiness="${3:-false}"
    allow_data="${4:-false}"
    fail_release="${5:-false}"
    PROJECT_DIR="/opt/hub"
    fail_static="${6:-false}"

    log() { printf 'LOG %s\n' "$*" >>"$events"; }
    check_route() { printf 'ROUTE %s %s\n' "$1" "$2" >>"$events"; }
    mikrotik_enabled() { return 0; }
    allow_operational_data() { [[ "$allow_data" == true ]]; }
    dc() {
        printf 'DC' >>"$events"
        printf ' <%s>' "$@" >>"$events"
        printf '\n' >>"$events"

        if [[ "$fail_static" == true && "$*" == *check-static-manifest.py* ]]; then
            exit 45
        fi
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
                if [[ "$fail_release" == true && "${!next}" == internet_release_audit ]]; then
                    exit 44
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

check_line="$(line_of 'DC <run> <--rm> <-T> <web> <python> <manage.py> <check>' "$events")"
migration_line="$(line_of 'DC <run> <--rm> <-T> <web> <python> <manage.py> <migrate> <--noinput>' "$events")"
readiness_line="$(line_of '<manage.py> <launch_readiness> <--json>' "$events")"
replacement_line="$(line_of 'DC <up> <-d> <--no-deps> <--no-build> <--force-recreate> <web> <management-mcp> <internet-worker> <notification-worker>' "$events")"
mcp_line="$(line_of 'DC <exec> <-T> <management-mcp> <python> <scripts/check-mcp-bridge.py>' "$events")"
static_line="$(line_of 'DC <run> <--rm> <-T> <web> <python> <scripts/check-static-manifest.py>' "$events")"
route_line="$(line_of 'ROUTE /menu/ 200' "$events")"
mikrotik_line="$(line_of 'DC <exec> <-T> <web> <python> <manage.py> <mikrotik_healthcheck> <--json>' "$events")"
runtime_readiness_line="$(line_of 'DC <exec> <-T> <web> <python> <manage.py> <internet_readiness> <--json>' "$events")"

(( check_line < migration_line ))
(( migration_line < readiness_line ))
(( readiness_line < replacement_line ))
(( static_line < replacement_line ))
(( replacement_line < mcp_line ))
(( mcp_line < route_line ))
(( route_line < mikrotik_line ))
(( mikrotik_line < runtime_readiness_line ))

# Explicitly retaining operational data must add only the documented readiness flag.
allowed_events="$tmp/allowed.events"
run_phase "$allowed_events" false false true
grep -Fq 'DC <run> <--rm> <-T> <--volume> </opt/hub/backups/production:/opt/hub/backups/production:ro> <web> <python> <manage.py> <launch_readiness> <--json> <--allow-operational-data>' "$allowed_events"

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
if grep -Fq 'DC <up> <-d> <--no-deps> <--no-build> <--force-recreate> <web> <management-mcp> <internet-worker> <notification-worker>' "$failed_events"; then
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
if grep -Fq 'DC <up> <-d> <--no-deps> <--no-build> <--force-recreate> <web> <management-mcp> <internet-worker> <notification-worker>' "$readiness_failed_events"; then
    echo "application replacement ran after a failed launch readiness gate" >&2
    exit 1
fi

# A failing Internet release audit must stop before any container replacement.
release_failed_events="$tmp/release-failure.events"
set +e
run_phase "$release_failed_events" false false false true
release_failure_status=$?
set -e
if (( release_failure_status == 0 )); then
    echo "Internet release audit FAIL unexpectedly allowed deployment to continue" >&2
    exit 1
fi
grep -Fq '<manage.py> <internet_release_audit> <--json>' "$release_failed_events"
if grep -Fq 'DC <up> <-d> <--no-deps> <--no-build> <--force-recreate> <web> <management-mcp> <internet-worker> <notification-worker>' "$release_failed_events"; then
    echo "application replacement ran after failed Internet release audit" >&2
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


static_failed_events="$tmp/static-failure.events"
set +e
run_phase "$static_failed_events" false false false false true
static_failure_status=$?
set -e
[[ "$static_failure_status" != 0 ]]
if grep -Fq 'DC <up>' "$static_failed_events"; then
    echo "traffic switched after static verification failure" >&2
    exit 1
fi

# Exercise the actual entry point with Git, Docker, and backup operations stubbed.
# Redirect the production checkout and lock into the test sandbox.
test_deploy="$tmp/deploy-production.sh"
sed -e 's|^PROJECT_DIR=.*|PROJECT_DIR="$TEST_PROJECT_DIR"|' \
    -e 's|^LOCK_FILE=.*|LOCK_FILE="$TEST_PROJECT_DIR/operation.lock"|' \
    "$repo/scripts/deploy-production.sh" >"$test_deploy"
grep -Fxq 'PROJECT_DIR="$TEST_PROJECT_DIR"' "$test_deploy"
grep -Fxq 'LOCK_FILE="$TEST_PROJECT_DIR/operation.lock"' "$test_deploy"

printf -v base_commit '%040d' 1
printf -v target_commit '%040d' 2
printf -v drift_commit '%040d' 3
printf -v base_id 'sha256:%064d' 1
base_image="ghcr.io/maximad/hub@$base_id"
printf -v target_image 'ghcr.io/maximad/hub@sha256:%064d' 2

run_full_deploy() (
    set -Eeuo pipefail
    scenario="$1"
    export TEST_PROJECT_DIR="$tmp/$scenario"
    export TEST_EVENTS="$tmp/$scenario.events"
    export ALLOW_PRELAUNCH_OPERATIONAL_DATA=false
    unset HUB_IMAGE
    mkdir -p "$TEST_PROJECT_DIR"/{.git,scripts,backups}
    : >"$TEST_EVENTS"
    printf '%s\n' "$base_commit" >"$TEST_PROJECT_DIR/checkout"
    printf '%s\n' "$base_commit" >"$TEST_PROJECT_DIR/backups/last_deployed_revision.txt"
    printf 'HUB_IMAGE=%s\n' "$target_image" >"$TEST_PROJECT_DIR/.env"
    touch "$TEST_PROJECT_DIR/docker-compose.prod.yml"

    cat >"$TEST_PROJECT_DIR/scripts/backup-production.sh" <<'BACKUP'
#!/usr/bin/env bash
set -eu
echo BACKUP >>"$TEST_EVENTS"
mkdir -p backups/production/hub-fixture
touch backups/production/hub-fixture/SUCCESS
BACKUP
    cat >"$TEST_PROJECT_DIR/scripts/verify-production-backup.sh" <<'RESTORE'
#!/usr/bin/env bash
set -eu
echo RESTORE >>"$TEST_EVENTS"
touch "$1/RESTORE_VERIFIED"
RESTORE
    chmod +x "$TEST_PROJECT_DIR"/scripts/*.sh

    id() {
        if [[ "$1" == -u ]]; then echo 1000; else echo deploy; fi
    }
    flock() { return 0; }
    python3() {
        printf 'CHECK COMPOSE IMAGE\n' >>"$TEST_EVENTS"
        [[ "$2" == *'json.load(sys.stdin)'* && "$3" == "$target_image" &&
           "$scenario" != compose-drift ]]
    }
    curl() {
        if [[ "${!#}" == */staff/* ]]; then echo 302; else echo 200; fi
    }

    git() {
        case "$1" in
            branch) echo main ;;
            status)
                if [[ "$scenario" == dirty ]]; then echo ' M fixture'; fi
                ;;
            rev-parse)
                if [[ "$2" == HEAD ]]; then
                    if [[ "$scenario" == checkout-drift ]]; then
                        echo "$drift_commit"
                    else
                        cat "$TEST_PROJECT_DIR/checkout"
                    fi
                elif [[ "$scenario" == main-drift ]]; then
                    echo "$drift_commit"
                else
                    echo "$target_commit"
                fi
                ;;
            fetch|merge-base) return 0 ;;
            merge)
                printf 'MERGE %s %s\n' "$2" "$3" >>"$TEST_EVENTS"
                [[ "$2" == --ff-only && "$3" == "$target_commit" ]]
                echo "$3" >"$TEST_PROJECT_DIR/checkout"
                ;;
            *) echo "Unexpected Git command: $1" >&2; return 1 ;;
        esac
    }

    docker() {
        case "$1" in
            info) return 0 ;;
            pull)
                printf 'PULL %s\n' "$2" >>"$TEST_EVENTS"
                [[ "$2" == "$target_image" && "$scenario" != pull-failure ]]
                ;;
            image)
                if [[ "$3" == "$base_image" ]]; then
                    printf '%s %s\n' "$base_id" "$base_commit"
                elif [[ "$3" == "$target_image" ]]; then
                    printf 'INSPECT %s\n' "$3" >>"$TEST_EVENTS"
                    if [[ "$scenario" == wrong-image-revision ]]; then
                        echo "$drift_commit"
                    else
                        echo "$target_commit"
                    fi
                else
                    return 1
                fi
                ;;
            container)
                if [[ "$scenario" == running-drift && "$3" == hub-internet-worker ]]; then
                    printf 'true %s %s\n' "$target_image" "$base_id"
                else
                    printf 'true %s %s\n' "$base_image" "$base_id"
                fi
                ;;
            compose)
                case "$6" in
                    config|logs|ps) return 0 ;;
                    run|up|exec) printf 'COMPOSE %s\n' "$6" >>"$TEST_EVENTS" ;;
                    *) echo "Unexpected Compose command: $6" >&2; return 1 ;;
                esac
                ;;
            *) echo "Unexpected Docker command: $1" >&2; return 1 ;;
        esac
    }

    if [[ "$scenario" == marker-drift ]]; then
        echo "$drift_commit" >"$TEST_PROJECT_DIR/backups/last_deployed_revision.txt"
    fi
    if [[ "$scenario" == manual ]]; then
        set --
        source "$test_deploy"
    else
        local_image="$target_image"
        if [[ "$scenario" == invalid-digest ]]; then
            local_image=ghcr.io/maximad/hub:latest
        fi
        source "$test_deploy" --pinned-release "$base_commit" "$base_image" "$target_commit" "$local_image"
    fi
)

assert_early_stop() {
    local scenario="$1" expected="$2" result
    set +e
    run_full_deploy "$scenario" >"$tmp/$scenario.output" 2>&1
    result=$?
    set -e
    if (( result == 0 )) || ! grep -Fq -- "$expected" "$tmp/$scenario.output"; then
        cat "$tmp/$scenario.output" >&2
        echo "Expected preflight rejection for $scenario" >&2
        exit 1
    fi
    if grep -Eq '^(BACKUP|RESTORE|MERGE|COMPOSE)' "$tmp/$scenario.events"; then
        cat "$tmp/$scenario.events" >&2
        echo "Production steps ran after a preflight mismatch in $scenario" >&2
        exit 1
    fi
}

assert_early_stop invalid-digest "CURRENT_SHA CURRENT_IMAGE TARGET_SHA TARGET_IMAGE"
assert_early_stop dirty "The repository contains local changes."
assert_early_stop checkout-drift "Production checkout differs from the approved starting commit."
assert_early_stop marker-drift "Last successful deployment differs from the approved starting commit."
assert_early_stop running-drift "Running release for hub-internet-worker differs from the approved starting image."
assert_early_stop main-drift "origin/main differs from the approved target commit."
assert_early_stop pull-failure "Could not pull the tested release image."
assert_early_stop wrong-image-revision "Release image revision does not match target commit."

set +e
run_full_deploy compose-drift >"$tmp/compose-drift.output" 2>&1
result=$?
set -e
[[ "$result" != 0 ]]
grep -Fq "Target Compose services do not all use the approved release image." "$tmp/compose-drift.output"
if grep -Eq '^COMPOSE (run|up)' "$tmp/compose-drift.events"; then
    echo "Compose ran or replaced a container with an unexpected image" >&2
    exit 1
fi

for scenario in pinned manual; do
    set +e
    run_full_deploy "$scenario" >"$tmp/$scenario.output" 2>&1
    result=$?
    set -e
    if (( result != 0 )); then
        cat "$tmp/$scenario.output" >&2
        exit 1
    fi
    events="$tmp/$scenario.events"
    pull_line="$(line_of "PULL $target_image" "$events")"
    inspect_line="$(line_of "INSPECT $target_image" "$events")"
    backup_line="$(line_of BACKUP "$events")"
    restore_line="$(line_of RESTORE "$events")"
    merge_line="$(line_of "MERGE --ff-only $target_commit" "$events")"
    run_line="$(line_of 'COMPOSE run' "$events")"
    (( pull_line < inspect_line && inspect_line < backup_line && backup_line < restore_line &&
       restore_line < merge_line && merge_line < run_line ))
    if [[ "$scenario" == pinned ]]; then
        config_line="$(line_of 'CHECK COMPOSE IMAGE' "$events")"
        (( merge_line < config_line && config_line < run_line ))
    fi
    [[ "$(<"$tmp/$scenario/backups/last_deployed_revision.txt")" == "$target_commit" ]]
done

echo "production release preflight tests passed"
