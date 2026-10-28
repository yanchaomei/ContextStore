#!/usr/bin/env bash
set -euo pipefail
root=/home/railtest/cswave1
cd "$root"
printf '%s\n' "$$" > "$root/evidence/server.pid"
date -u +%FT%TZ > "$root/evidence/server.started"
exec env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C \
  CS_RDMA_SLAB_MB=64 CS_FORCE_DISK_READ=1 \
  CS_RDMA_DEVICES=rxe0:10.81.1.1:25353:1,rxe1:10.82.1.1:25354:1 \
  "$root/bin/contextstore-server" --config "$root/server.toml" --log-level info \
  > "$root/evidence/server.log" 2>&1
