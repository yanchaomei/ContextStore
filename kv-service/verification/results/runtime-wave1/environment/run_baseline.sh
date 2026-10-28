#!/usr/bin/env bash
set -uo pipefail
WAVE1_ROOT=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
source "$WAVE1_ROOT/env.sh"
cd "$WAVE1_ROOT/src-baseline" || exit 1
evidence="$WAVE1_ROOT/evidence"
date -u +%Y-%m-%dT%H:%M:%SZ > "$evidence/baseline.started"
{
    hostname
    uname -a
    rustc -vV
    cargo -vV
    cc --version
    protoc --version
    sha256sum "$WAVE1_ROOT/tools/rust/bin/rustc" "$WAVE1_ROOT/tools/rust/bin/cargo" "$CARGO_HOME/config.toml" Cargo.lock
    df -h "$WAVE1_ROOT"
    free -h
} > "$evidence/toolchain_receipt.txt" 2>&1

run_test() {
    local label="$1"
    shift
    printf '%s\n' "$*" > "$evidence/$label.command"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$evidence/$label.started"
    "$@" > "$evidence/$label.log" 2>&1
    local status=$?
    printf '%s\n' "$status" > "$evidence/$label.exit_code"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$evidence/$label.finished"
}

run_test baseline_client_rdma cargo test --locked --offline -j 4 -p contextstore-client-rs --features rdma
run_test baseline_workspace_default cargo test --locked --offline -j 4
date -u +%Y-%m-%dT%H:%M:%SZ > "$evidence/baseline.finished"

