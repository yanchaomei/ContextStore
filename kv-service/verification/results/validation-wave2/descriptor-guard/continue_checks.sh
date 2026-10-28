#!/usr/bin/env bash
set -eu
source /home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/env.sh
GUARD_ROOT="$WAVE1_ROOT/wave2/descriptor-guard"
export CARGO_TARGET_DIR="$GUARD_ROOT/target"
export CARGO_NET_OFFLINE=true
cp -a "$GUARD_ROOT/src" "$GUARD_ROOT/baseline-src"
cp "$GUARD_ROOT/baseline-server.rs" "$GUARD_ROOT/baseline-src/kv-service/server/src/rdma/server.rs"
run_check() {
    local name="$1"; shift
    printf '%q ' "$@" > "$GUARD_ROOT/logs/$name.command"
    printf '\n' >> "$GUARD_ROOT/logs/$name.command"
    date -u +%FT%TZ > "$GUARD_ROOT/logs/$name.start"
    set +e
    "$@" > "$GUARD_ROOT/logs/$name.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$GUARD_ROOT/logs/$name.exit"
    date -u +%FT%TZ > "$GUARD_ROOT/logs/$name.end"
    printf '%s exit=%s\n' "$name" "$rc"
}
cd "$GUARD_ROOT/src"
run_check clippy-default-json cargo clippy --offline --locked -p contextstore-server --all-targets --message-format=json -- -D warnings
run_check clippy-rdma-json cargo clippy --offline --locked -p contextstore-server --all-targets --features rdma,io-uring,metrics --message-format=json -- -D warnings
cd "$GUARD_ROOT/baseline-src"
run_check baseline-clippy-default-json cargo clippy --offline --locked -p contextstore-server --all-targets --message-format=json -- -D warnings
run_check baseline-clippy-rdma-json cargo clippy --offline --locked -p contextstore-server --all-targets --features rdma,io-uring,metrics --message-format=json -- -D warnings
cd "$GUARD_ROOT/src"
run_check make-build make build
cat "$GUARD_ROOT/logs/make-build.exit"
tail -12 "$GUARD_ROOT/logs/make-build.log"
