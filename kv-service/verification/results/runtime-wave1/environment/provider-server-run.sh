#!/usr/bin/env bash
set -euo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_provider_20261029
cd "$root"
printf '%s\n' "$$" > "$root/evidence/server.pid"
date -u +%FT%TZ > "$root/evidence/server.started"
exec env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C \
  CS_RDMA_SLAB_MB=64 CS_FORCE_DISK_READ=1 \
  CS_RDMA_DEVICES=mlx5_1:10.0.0.64:25353:3,mlx5_1:10.0.0.64:25354:3 \
  "$root/bin/contextstore-server" --config "$root/server.toml" --log-level info \
  > "$root/evidence/server.log" 2>&1
