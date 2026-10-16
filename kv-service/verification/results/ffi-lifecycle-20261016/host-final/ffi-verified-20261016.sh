#!/usr/bin/env bash
set -euo pipefail
cd /home/kvgroup/chaomei/contextstore_multirail_20261002
out=evidence/ffi-lifecycle-20261016-verified
mkdir -p "$out/build-receipt/source-snapshot"
mapfile -t sources < evidence/ffi-lifecycle-20261015/build-receipt/sources.txt
sha256sum "${sources[@]}" > "$out/build-receipt/source-before.sha256"
for source in "${sources[@]}"; do mkdir -p "$out/build-receipt/source-snapshot/$(dirname "$source")"; cp "$source" "$out/build-receipt/source-snapshot/$source"; done
rustc -Vv > "$out/build-receipt/rustc.txt"
cargo -V > "$out/build-receipt/cargo.txt"
cargo build -p contextstore-rdma-ffi --release > "$out/build-receipt/build.log" 2>&1
sha256sum "${sources[@]}" > "$out/build-receipt/source-after.sha256"
cmp "$out/build-receipt/source-before.sha256" "$out/build-receipt/source-after.sha256"
sha256sum target/release/libcontextstore_rdma_ffi.so > "$out/build-receipt/binary.sha256"

set +e
#!/usr/bin/env bash
set -uo pipefail
cd /home/kvgroup/chaomei/contextstore_multirail_20261002
out=evidence/ffi-lifecycle-20261016-verified
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

check black /home/kvgroup/.local/bin/uvx --offline --from black==24.8.0 black --check src/contextstore/storage/rdma_client.py tests/unit/test_rdma_client_lifecycle.py kv-service/verification/ffi_lifecycle_probe.py kv-service/verification/ffi_rxe_late_write.py kv-service/verification/ffi_rxe_put_reconnect.py kv-service/verification/run_ffi_lifecycle_validation.py kv-service/verification/test_ffi_validation_receipts.py
check ruff /home/kvgroup/.local/bin/uvx --offline --from ruff==0.6.9 ruff check src/contextstore/storage/rdma_client.py tests/unit/test_rdma_client_lifecycle.py kv-service/verification/ffi_lifecycle_probe.py kv-service/verification/ffi_rxe_late_write.py kv-service/verification/ffi_rxe_put_reconnect.py kv-service/verification/run_ffi_lifecycle_validation.py kv-service/verification/test_ffi_validation_receipts.py
sha256sum src/contextstore/storage/rdma_client.py tests/unit/test_rdma_client_lifecycle.py kv-service/verification/ffi_lifecycle_probe.py kv-service/verification/ffi_rxe_late_write.py kv-service/verification/ffi_rxe_put_reconnect.py kv-service/verification/run_ffi_lifecycle_validation.py kv-service/verification/test_ffi_validation_receipts.py > "$out/python-sources.sha256"
python3 --version > "$out/python-version.txt"
