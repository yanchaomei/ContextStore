#!/usr/bin/env bash
set -uo pipefail
cd /home/kvgroup/chaomei/contextstore_multirail_20261002
out=evidence/ffi-lifecycle-20261016-round1
mkdir -p "$out"
check() { local label="$1"; shift; "$@" > "$out/$label.log" 2>&1; local rc=$?; echo "$rc" > "$out/$label.exit"; echo "$label exit=$rc"; }
check ffi-build cargo build -p contextstore-rdma-ffi --release
check clippy cargo clippy -p contextstore-rdma-ffi --release -- -D warnings
check rustfmt cargo fmt -p contextstore-rdma-ffi -- --check
check python-full env PYTHONPATH=src python3 -m pytest tests/ -v
check receipt-tests python3 kv-service/verification/test_ffi_validation_receipts.py -v
check make-build make build
check test-server make test-server
check shim-build cc -shared -fPIC -Wall -Wextra -Werror -o "$out/verbs_failstop.so" kv-service/verification/verbs_failstop_shim.c -ldl
check cq-shim-build cc -shared -fPIC -Wall -Wextra -Werror -o "$out/verbs_cq_delay.so" kv-service/verification/verbs_cq_delay_shim.c -ldl
check native python3 kv-service/verification/run_ffi_lifecycle_validation.py --library target/release/libcontextstore_rdma_ffi.so --shim "$out/verbs_failstop.so" --device mlx5_1 --gid 3 --source-commit b6d1cd9-working-tree-20261016 --require-timeout --output "$out/native"
