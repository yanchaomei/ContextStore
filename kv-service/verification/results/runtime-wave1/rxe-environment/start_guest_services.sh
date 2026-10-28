#!/usr/bin/env bash
set -euo pipefail
root=/home/railtest/cswave1
redis-server --bind 127.0.0.1 --port 26379 --save '' --appendonly no \
    --dir "$root/redis" --daemonize yes --pidfile "$root/evidence/redis.pid" \
    --logfile "$root/evidence/redis.log"
redis-cli -h 127.0.0.1 -p 26379 ping > "$root/evidence/redis_health.txt"
nohup bash "$root/guest_server_run.sh" > "$root/evidence/server_launcher.log" 2>&1 < /dev/null &
printf '%s\n' "$!" > "$root/evidence/server_launcher.pid"
