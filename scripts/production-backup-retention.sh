#!/usr/bin/env bash

# Shared retention helpers for production backups. This file is sourced by the
# backup script and can also be sourced by the shell test suite.

production_backup_timestamp_epoch() {
    local name="$1" timestamp iso epoch

    [[ "$name" =~ ^hub-([0-9]{8})T([0-9]{6})Z$ ]] || return 1
    timestamp="${BASH_REMATCH[1]}${BASH_REMATCH[2]}"
    iso="${timestamp:0:4}-${timestamp:4:2}-${timestamp:6:2}T${timestamp:8:2}:${timestamp:10:2}:${timestamp:12:2}Z"

    epoch="$(date -u -d "$iso" +%s 2>/dev/null)" || return 1
    # GNU date normalizes some impossible dates, so require an exact round trip.
    [[ "$(date -u -d "@$epoch" +%Y-%m-%dT%H:%M:%SZ)" == "$iso" ]] || return 1
    printf '%s\n' "$epoch"
}

cleanup_production_backups() {
    local backup_root="$1" retention_days="$2"
    local root_real candidate base epoch newest="" newest_epoch=-1 cutoff i
    local -a candidates=() epochs=()

    [[ "$backup_root" == /* && "$backup_root" != "/" ]] || return 1
    root_real="$(cd "$backup_root" && pwd -P)" || return 1
    [[ "$root_real" == "$backup_root" ]] || return 1
    [[ "$retention_days" =~ ^[0-9]+$ ]] || return 1

    while IFS= read -r candidate; do
        [[ "$(dirname -- "$candidate")" == "$root_real" && ! -L "$candidate" ]] || continue
        [[ -f "$candidate/SUCCESS" ]] || continue
        base="$(basename -- "$candidate")"
        epoch="$(production_backup_timestamp_epoch "$base")" || continue
        candidates+=("$candidate")
        epochs+=("$epoch")
        if ((epoch > newest_epoch)); then
            newest_epoch="$epoch"
            newest="$candidate"
        fi
    done < <(find "$root_real" -mindepth 1 -maxdepth 1 -type d -print)

    ((${#candidates[@]} > 1)) || return 0
    cutoff="$(date -u -d "$retention_days days ago" +%s)"
    for ((i=0; i<${#candidates[@]}; i++)); do
        candidate="${candidates[i]}"
        [[ "$candidate" != "$newest" ]] || continue
        # Recheck containment and type immediately before removal.
        [[ "$(dirname -- "$candidate")" == "$root_real" && ! -L "$candidate" ]] || continue
        if (( epochs[i] < cutoff )); then
            rm -rf -- "$candidate"
        fi
    done
    return 0
}
