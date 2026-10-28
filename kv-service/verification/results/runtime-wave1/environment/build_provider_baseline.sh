#!/usr/bin/env bash
set -euo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
source "$root/env.sh"
export CARGO_TARGET_DIR="$root/target-provider-baseline"
cd "$root/src-baseline"
mkdir -p "$root/evidence/provider-baseline" "$TMPDIR"
receipt="$root/evidence/provider-baseline"
date -u +%FT%TZ > "$receipt/build.started"
sha256sum -c "$root/source_sha256.txt" > "$receipt/source_hash_before_build.txt"
set +e
cargo build --locked --offline -j 4 -p contextstore-server --features rdma --bin contextstore-server > "$receipt/server_build.log" 2>&1
status=$?
printf '%s\n' "$status" > "$receipt/server_build.exit_code"
if [ "$status" -ne 0 ]; then exit "$status"; fi
cargo test --locked --offline -j 4 -p contextstore-client-rs --features rdma --test rdma_e2e --test rail_read_e2e --no-run > "$receipt/client_build.log" 2>&1
status=$?
printf '%s\n' "$status" > "$receipt/client_build.exit_code"
if [ "$status" -ne 0 ]; then exit "$status"; fi
sha256sum "$CARGO_TARGET_DIR/debug/contextstore-server" > "$receipt/server_binary.sha256"
ldd "$CARGO_TARGET_DIR/debug/contextstore-server" > "$receipt/server_binary.ldd"
sha256sum -c "$root/source_sha256.txt" > "$receipt/source_hash_after_build.txt"
date -u +%FT%TZ > "$receipt/build.finished"
