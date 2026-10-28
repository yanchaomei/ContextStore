#!/usr/bin/env bash
set -eu
source /home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/env.sh
GUARD_ROOT="$WAVE1_ROOT/wave2/descriptor-guard"
export CARGO_NET_OFFLINE=true
mkdir -p "$GUARD_ROOT/native-frozen-bin"
for variant in candidate baseline; do
    if [ "$variant" = candidate ]; then source_dir=src; else source_dir=baseline-src; fi
    export CARGO_TARGET_DIR="$GUARD_ROOT/native-target-$variant"
    cd "$GUARD_ROOT/$source_dir"
    date -u +%FT%TZ > "$GUARD_ROOT/logs/native-server-$variant-build.start"
    set +e
    cargo build --offline --locked -p contextstore-server --features rdma --bin contextstore-server > "$GUARD_ROOT/logs/native-server-$variant-build.log" 2>&1
    rc=$?
    set -e
    printf '%s\n' "$rc" > "$GUARD_ROOT/logs/native-server-$variant-build.exit"
    date -u +%FT%TZ > "$GUARD_ROOT/logs/native-server-$variant-build.end"
    if [ "$rc" -ne 0 ]; then exit "$rc"; fi
    cp "$CARGO_TARGET_DIR/debug/contextstore-server" "$GUARD_ROOT/native-frozen-bin/$variant-server-debug-rdma"
    sha256sum "$GUARD_ROOT/native-frozen-bin/$variant-server-debug-rdma"
done
python3 - "$GUARD_ROOT" <<'PYINNER'
from pathlib import Path
import hashlib,json,shutil,sys,datetime
root=Path(sys.argv[1]); executable=None
for line in (root/'logs/native-probe-build.log').read_text().splitlines():
    try:d=json.loads(line)
    except ValueError:continue
    if d.get('reason')=='compiler-artifact' and d.get('target',{}).get('name')=='descriptor_guard_probe' and d.get('executable'):executable=Path(d['executable'])
assert executable is not None
frozen_probe=root/'native-frozen-bin/descriptor-guard-probe';shutil.copy2(executable,frozen_probe)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
manifest=json.loads((root/'green-source-sha256.json').read_text())
differences=[name for name in manifest if sha(root/'src'/name)!=sha(root/'baseline-src'/name)]
assert differences==['kv-service/server/src/rdma/server.rs'],differences
assert sha(root/'native-frozen-bin/candidate-server-debug-rdma')!=sha(root/'native-frozen-bin/baseline-server-debug-rdma'), 'independent builds must not reuse the same binary'
receipt={'state':'BUILT_NOT_RUN','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'profile':'debug','server_features':['rdma'],'server_command':'cargo build --offline --locked -p contextstore-server --features rdma --bin contextstore-server','common_config_sha256':sha(root/'native-server.toml'),'probe_source_sha256':sha(root/'native_probe.rs'),'fixed_client_source_sha256':sha(root/'src/kv-service/client-rs/src/rdma.rs'),'production_source_differences':differences,'binaries':{p.name:sha(p) for p in (root/'native-frozen-bin').iterdir()},'server_sources':{'baseline':sha(root/'baseline-src/kv-service/server/src/rdma/server.rs'),'candidate':sha(root/'src/kv-service/server/src/rdma/server.rs')},'native_execution':'deferred until parent grants guest window'}
(root/'native-freeze.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
PYINNER
