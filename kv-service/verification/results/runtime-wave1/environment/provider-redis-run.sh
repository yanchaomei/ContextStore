#!/usr/bin/env bash
set -euo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_provider_20261029
printf '%s\n' "$$" > "$root/evidence/redis.pid"
date -u +%FT%TZ > "$root/evidence/redis.started"
exec env -i PATH=/usr/bin:/bin LANG=C \
  redis-server --bind 127.0.0.1 --port 26379 --save '' --appendonly no \
  --dir "$root/redis" > "$root/evidence/redis.log" 2>&1
