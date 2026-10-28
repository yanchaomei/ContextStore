#!/usr/bin/env bash
set -uo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
source "$root/env.sh"
source "$root/provider-client.env"
export CARGO_TARGET_DIR="$root/target-provider-baseline"
cd "$root/src-baseline" || exit 1
receipt="$root/evidence/provider-baseline"
date -u +%FT%TZ > "$receipt/tests.started"
{
    hostname
    sha256sum "$root/provider-client.env" "$root/run_provider_baseline.sh" Cargo.lock
    find "$CARGO_TARGET_DIR/debug/deps" -maxdepth 1 -type f -executable \( -name 'rdma_e2e-*' -o -name 'rail_read_e2e-*' \) -exec sha256sum {} \;
    rdma link show
    rdma resource show
} > "$receipt/client_pretest.txt" 2>&1

run_case() {
    local suite="$1"
    local name="$2"
    date -u +%FT%TZ > "$receipt/$name.started"
    printf '%s\n' "timeout 90s cargo test --locked --offline -j 4 -p contextstore-client-rs --features rdma --test $suite $name -- --ignored --exact --nocapture --test-threads=1" > "$receipt/$name.command"
    timeout 90s cargo test --locked --offline -j 4 -p contextstore-client-rs --features rdma --test "$suite" "$name" -- --ignored --exact --nocapture --test-threads=1 > "$receipt/$name.log" 2>&1
    local status=$?
    printf '%s\n' "$status" > "$receipt/$name.exit_code"
    date -u +%FT%TZ > "$receipt/$name.finished"
    return "$status"
}

run_case rdma_e2e rdma_put_and_get_round_trip
run_case rail_read_e2e single_real_rail_restores_verified_object
run_case rail_read_e2e same_object_matches_single_and_dual_rail
rdma resource show > "$receipt/client_resources_after_tests.txt" 2>&1
sha256sum -c "$root/source_sha256.txt" > "$receipt/source_hash_after_tests.txt"
date -u +%FT%TZ > "$receipt/tests.finished"
