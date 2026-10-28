#!/usr/bin/env bash
set -uo pipefail
WAVE1_ROOT=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
source "$WAVE1_ROOT/env.sh"
mkdir -p "$TMPDIR"
cd "$WAVE1_ROOT/src-baseline" || exit 1
evidence="$WAVE1_ROOT/evidence"
date -u +%Y-%m-%dT%H:%M:%SZ > "$evidence/configured.started"

run_test() {
    local label="$1"
    shift
    printf '%s\n' "$*" > "$evidence/$label.command"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$evidence/$label.started"
    "$@" > "$evidence/$label.log" 2>&1
    local status=$?
    printf '%s\n' "$status" > "$evidence/$label.exit_code"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$evidence/$label.finished"
    return "$status"
}

if ! run_test baseline_server_build cargo build --locked --offline -j 4 -p contextstore-server --bin contextstore-server; then
    date -u +%Y-%m-%dT%H:%M:%SZ > "$evidence/configured.finished"
    exit 1
fi
export CS_E2E_SERVER_BIN="$CARGO_TARGET_DIR/debug/contextstore-server"
{
    printf 'CS_E2E_SERVER_BIN=%s\nTMPDIR=%s\n' "$CS_E2E_SERVER_BIN" "$TMPDIR"
    redis-server --version
    sha256sum "$CS_E2E_SERVER_BIN" "$(command -v redis-server)"
} > "$evidence/e2e_environment.txt"
run_test configured_client_rdma cargo test --locked --offline -j 4 -p contextstore-client-rs --features rdma
run_test configured_workspace_default cargo test --locked --offline -j 4
sha256sum -c ../source_sha256.txt > "$evidence/source_hash_after_tests.txt"
date -u +%Y-%m-%dT%H:%M:%SZ > "$evidence/configured.finished"
