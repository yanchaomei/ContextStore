#!/usr/bin/env bash
set -eu
variant="$1"
case "$variant" in baseline|candidate) ;; *) exit 2 ;; esac
S2ROOT=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
GUARD_ROOT="$S2ROOT/wave2/descriptor-guard"
RUN_ROOT="$GUARD_ROOT/native-run-20261029-v1"
mkdir "$RUN_ROOT/$variant"
bash "$S2ROOT/vm-rxe/guest_ssh.sh" server "bash -s -- $variant" <<'SERVER'
set -eu
variant="$1"
root=/home/railtest/wave2-descriptor-guard
run="$root/$variant"
mkdir "$run"
mkdir -p "$root/data/rail0" "$root/data/rail1"
if ss -ltn | grep -Eq ':(55451|55453|6396) '; then exit 3; fi
redis-server --bind 127.0.0.1 --port 6396 --save '' --appendonly no --daemonize yes --pidfile "$run/redis.pid" --logfile "$run/redis.log" --dir "$run"
redis-cli -p 6396 ping > "$run/redis-ping.txt"
date -u +%FT%TZ > "$run/server.start"
nohup env CS_FORCE_DISK_READ=1 CS_RDMA_SLAB_MB=16 CS_RDMA_CQ_TIMEOUT_MS=300 CS_RDMA_DEVICES=rxe0:10.81.1.1:55453:1 "$root/native-frozen-bin/$variant-server-debug-rdma" --config "$root/server.toml" --log-level info > "$run/server.log" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > "$run/server.pid"
python3 - "$pid" "$run" <<'PYID'
from pathlib import Path
import json,sys,time
pid=int(sys.argv[1]);run=Path(sys.argv[2]);p=Path(f'/proc/{pid}')
r={'pid':pid,'exe':str((p/'exe').resolve()),'cmdline':(p/'cmdline').read_bytes().decode().split('\0'),'stat':(p/'stat').read_text(),'utc_unix':time.time()}
(run/'server-identity.json').write_text(json.dumps(r,indent=2)+'\n')
PYID
for attempt in 1 2 3 4 5; do
    kill -0 "$pid"
    if ss -ltn | grep -q ':55451 ' && ss -ltn | grep -q ':55453 '; then break; fi
    sleep 1
done
ss -ltn | grep -E ':(55451|55453|6396) ' > "$run/listeners.txt"
cat "$run/server-identity.json"
SERVER
bash "$S2ROOT/vm-rxe/guest_ssh.sh" client "bash -s -- $variant" <<'CLIENT'
set -eu
variant="$1"
root=/home/railtest/wave2-descriptor-guard
run="$root/$variant"
mkdir "$run"
date -u +%FT%TZ > "$run/client.start"
set +e
env CS_GUARD_GRPC_ENDPOINT=http://10.81.1.1:55451 CS_GUARD_RDMA_ENDPOINT=10.81.1.1:55453 CS_GUARD_RDMA_DEVICE=rxe0 CS_GUARD_RDMA_GID=1 "$root/native-frozen-bin/descriptor-guard-probe" --ignored --nocapture --exact descriptor_get_retirement_keeps_private_target_and_fresh_reads_correct > "$run/client.log" 2>&1 &
pid=$!
printf '%s\n' "$pid" > "$run/client.pid"
python3 - "$pid" "$run" <<'PYID'
from pathlib import Path
import json,sys,time
pid=int(sys.argv[1]);run=Path(sys.argv[2]);p=Path(f'/proc/{pid}')
r={'pid':pid,'exe':str((p/'exe').resolve()),'cmdline':(p/'cmdline').read_bytes().decode().split('\0'),'stat':(p/'stat').read_text(),'utc_unix':time.time()}
(run/'client-identity.json').write_text(json.dumps(r,indent=2)+'\n')
PYID
wait "$pid"
rc=$?
set -e
printf '%s\n' "$rc" > "$run/client.exit"
date -u +%FT%TZ > "$run/client.end"
cat "$run/client.log"
CLIENT
bash "$S2ROOT/vm-rxe/guest_ssh.sh" server "bash -s -- $variant" <<'STOP'
set -eu
variant="$1"
root=/home/railtest/wave2-descriptor-guard
run="$root/$variant"
pid=$(cat "$run/server.pid")
expected="$root/native-frozen-bin/$variant-server-debug-rdma"
test "$(readlink /proc/$pid/exe)" = "$expected"
ps -p "$pid" -o pid,ppid,etimes,args > "$run/server-after-client.txt"
kill -TERM "$pid"
redis_pid=$(cat "$run/redis.pid")
kill -TERM "$redis_pid"
date -u +%FT%TZ > "$run/server.stop"
cat "$run/server.log"
STOP
for role in server client; do
    bash "$S2ROOT/vm-rxe/guest_ssh.sh" "$role" "tar -czf - -C /home/railtest/wave2-descriptor-guard $variant" > "$RUN_ROOT/$variant/$role-evidence.tar.gz"
    mkdir "$RUN_ROOT/$variant/$role"
    tar -xzf "$RUN_ROOT/$variant/$role-evidence.tar.gz" --strip-components=1 -C "$RUN_ROOT/$variant/$role"
done
