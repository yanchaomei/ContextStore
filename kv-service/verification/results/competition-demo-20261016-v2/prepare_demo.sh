#!/usr/bin/env bash
set -euo pipefail
repo=${1:?usage: prepare_demo.sh REPO_ROOT DEMO_DIR}
dest=${2:?usage: prepare_demo.sh REPO_ROOT DEMO_DIR}
here=$(cd "$(dirname "$0")" && pwd)
if [[ ! -f "$repo/target/release/cs-rail-read-bench" || ! -f "$repo/target/release/libcontextstore_rdma_ffi.so" ]]; then
  echo 'Build the Linux RDMA server/client/FFI first with make build.' >&2
  exit 2
fi
mkdir -p "$dest"
tar -xzf "$repo/kv-service/verification/results/ffi-lifecycle-20261016/binaries-rxe.tar.gz" -C "$dest"
cp "$repo/target/release/libcontextstore_rdma_ffi.so" "$dest/candidate.so"
cp "$repo/target/release/cs-rail-read-bench" "$dest/"
cp "$repo/src/contextstore/storage/rdma_client.py" "$dest/"
cp "$repo/kv-service/verification/ffi_rxe_late_write.py" "$repo/kv-service/verification/ffi_rxe_put_reconnect.py" "$dest/"
cp "$here/run_demo.py" "$here/record_terminal.py" "$dest/"
cc -shared -fPIC -Wall -Wextra -Werror "$repo/kv-service/verification/verbs_cq_delay_shim.c" -o "$dest/verbs_cq_delay.so" -ldl
(cd "$dest" && sha256sum candidate.so baseline.so verbs_cq_delay.so cs-rail-read-bench rdma_client.py run_demo.py)
echo 'Prepared the recorded fixture. It requires the documented RXE server, devices and routes.'
