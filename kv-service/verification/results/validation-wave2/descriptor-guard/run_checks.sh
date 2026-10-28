#!/usr/bin/env bash
set -eu
source /home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/env.sh
GUARD_ROOT="$WAVE1_ROOT/wave2/descriptor-guard"
export CARGO_TARGET_DIR="$GUARD_ROOT/target"
export CARGO_NET_OFFLINE=true
GUARD_REDIS_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
mkdir -p "$GUARD_ROOT/redis" "$GUARD_ROOT/logs"
redis-server --bind 127.0.0.1 --port "$GUARD_REDIS_PORT" --save '' --appendonly no --daemonize yes --pidfile "$GUARD_ROOT/redis/pid" --logfile "$GUARD_ROOT/redis/redis.log" --dir "$GUARD_ROOT/redis"
trap 'kill "$(cat "$GUARD_ROOT/redis/pid")"' EXIT
export CS_REDIS_URL="redis://127.0.0.1:$GUARD_REDIS_PORT/"
for attempt in 1 2 3 4 5; do
    if redis-cli -p "$GUARD_REDIS_PORT" ping > "$GUARD_ROOT/logs/redis-ping.log"; then break; fi
    sleep 1
done
cd "$GUARD_ROOT/src"
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
    tail -8 "$GUARD_ROOT/logs/$name.log"
    if [ "$rc" -ne 0 ]; then return "$rc"; fi
}
run_check rustfmt rustfmt --edition 2021 --check kv-service/server/src/rdma/server.rs
run_check server-default make test-server BUILD_TYPE=debug
run_check server-rdma cargo test --offline --locked -p contextstore-server --features rdma,io-uring,metrics
run_check clippy-default cargo clippy --offline --locked -p contextstore-server --all-targets -- -D warnings
run_check clippy-rdma cargo clippy --offline --locked -p contextstore-server --all-targets --features rdma,io-uring,metrics -- -D warnings
run_check make-build make build
