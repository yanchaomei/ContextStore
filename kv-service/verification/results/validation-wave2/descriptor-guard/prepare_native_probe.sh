#!/usr/bin/env bash
set -eu
source /home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/env.sh
GUARD_ROOT="$WAVE1_ROOT/wave2/descriptor-guard"
export CARGO_TARGET_DIR="$GUARD_ROOT/target"
export CARGO_NET_OFFLINE=true
cp -a "$GUARD_ROOT/src" "$GUARD_ROOT/probe-src"
cp "$GUARD_ROOT/native_probe.rs" "$GUARD_ROOT/probe-src/kv-service/client-rs/tests/descriptor_guard_probe.rs"
cd "$GUARD_ROOT/probe-src"
date -u +%FT%TZ > "$GUARD_ROOT/logs/native-probe-build.start"
set +e
cargo test --offline --locked -p contextstore-client-rs --features rdma --test descriptor_guard_probe --no-run --message-format=json > "$GUARD_ROOT/logs/native-probe-build.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$GUARD_ROOT/logs/native-probe-build.exit"
date -u +%FT%TZ > "$GUARD_ROOT/logs/native-probe-build.end"
exit "$rc"
