#!/usr/bin/env bash
set -euo pipefail

# Test-only: run in the isolated RXE server guest while its Redis session is up.
# A separate client process must issue a descriptor GET to trigger QP/MR cleanup.
operation=${1:?usage: launch_server_failstop.sh destroy_qp|dereg_mr OUTPUT_DIR [SLAB_MB]}
output_dir=${2:?usage: launch_server_failstop.sh destroy_qp|dereg_mr OUTPUT_DIR [SLAB_MB]}
case "$operation" in
  destroy_qp) slab_mb=128 ;;
  dereg_mr) slab_mb=${3:-16} ;;
  *) echo "unsupported operation: $operation" >&2; exit 2 ;;
esac
mkdir -p "$output_dir"
{
  printf 'operation=%s\nslab_mb=%s\n' "$operation" "$slab_mb"
  sha256sum /home/railtest/bin/contextstore-server \
    /home/railtest/libverbs_failstop.so \
    /home/railtest/evidence/server.toml \
    "$0"
} > "$output_dir/launch-manifest.txt"
ulimit -c 0
set +e
LD_PRELOAD=/home/railtest/libverbs_failstop.so \
CS_FAIL_VERBS_OP="$operation" \
CS_FAIL_VERBS_LOG="$output_dir/verbs.events" \
CS_RDMA_DEVICES='rxe_s0:10.31.0.2:55153:1,rxe_s1:10.32.0.2:55154:1' \
CS_RDMA_SLAB_MB="$slab_mb" \
CS_RDMA_CQ_TIMEOUT_MS=30000 \
/home/railtest/bin/contextstore-server --config /home/railtest/evidence/server.toml \
  > "$output_dir/server.log" 2>&1
server_exit_code=$?
set -e
printf '%s\n' "$server_exit_code" > "$output_dir/exit-code.txt"
