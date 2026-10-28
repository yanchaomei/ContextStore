#!/usr/bin/env python3
"""Run only after parent GO. Preserve a new directory for every attempt."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

ROOT = Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029')
SOURCE = ROOT / 'src-candidate'
DEFAULT_FIXTURE_SHA = '1859ffb78812886458a27f52cbb9f8cd05ff04047982ff2621c1b081485bda08'

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def source_hashes():
    excluded = ('.git/', 'target/', 'docs/', 'outputs/', 'kv-service/verification/results/', 'kv-service/benchmarks/results/')
    files = {}
    for directory, dirs, names in os.walk(SOURCE):
        folder = Path(directory)
        dirs[:] = [d for d in dirs if not ((folder/d).relative_to(SOURCE).as_posix()+'/').startswith(excluded)]
        for name in names:
            path=folder/name
            files[path.relative_to(SOURCE).as_posix()] = digest(path)
    return files

def pilot_hashes():
    files={}
    for entry in ROOT.glob('pilot-frozen-bin*'):
        candidates=[entry] if entry.is_file() else list(entry.iterdir())
        for path in candidates:
            if path.is_file():files[str(path.relative_to(ROOT))]=digest(path)
    return files

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-label', required=True)
    parser.add_argument('--changed-files', type=Path, required=True)
    args = parser.parse_args()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = ROOT / 'final-verification' / ('host-' + stamp)
    output.mkdir(parents=True, exist_ok=False)
    (output/'tmp').mkdir()
    env = os.environ.copy()
    env.update(PATH=str(ROOT/'tools/rust/bin') + ':' + env.get('PATH', ''),
               CARGO_HOME=str(ROOT/'tools/cargo'), CARGO_TARGET_DIR=str(ROOT/'target-candidate'),
               CARGO_BUILD_JOBS='4', LIBCLANG_PATH='/usr/lib/x86_64-linux-gnu',
               PROTOC='/usr/local/bin/protoc', TMPDIR=str(output/'tmp'))
    for key in ['MAKEFLAGS','LD_PRELOAD','SERVER_FEATURES','CLIENT_RS_FEATURES','BUILD_TYPE','RUSTFLAGS','CARGO_ENCODED_RUSTFLAGS']:
        env.pop(key, None)
    results = {}

    def run(label, command, command_env=None, cwd=SOURCE):
        used = env if command_env is None else command_env
        manifest = {'command': command, 'cwd': str(cwd), 'started': now(),
                    'environment': {k: used[k] for k in ['CARGO_HOME','CARGO_TARGET_DIR','CARGO_BUILD_JOBS','LIBCLANG_PATH','PROTOC','TMPDIR','CS_E2E_SERVER_BIN','CS_REDIS_URL'] if k in used}}
        (output/(label+'.command.json')).write_text(json.dumps(manifest, indent=2)+'\n')
        with (output/(label+'.stdout')).open('wb') as stdout, (output/(label+'.stderr')).open('wb') as stderr:
            proc = subprocess.run(command, cwd=cwd, env=used, stdout=stdout, stderr=stderr)
        (output/(label+'.exit_code')).write_text(str(proc.returncode)+'\n')
        results[label] = {'exit_code': proc.returncode, 'started': manifest['started'], 'finished': now(),
                          'stdout_sha256': digest(output/(label+'.stdout')), 'stderr_sha256': digest(output/(label+'.stderr'))}
        (output/'progress.json').write_text(json.dumps(results, indent=2)+'\n')
        return proc.returncode

    before = source_hashes()
    (output/'source_before.json').write_text(json.dumps(before, sort_keys=True, indent=2)+'\n')
    expected=json.loads((args.changed_files.parent/'expected_final_sources.json').read_text())
    assert all(before.get(name)==value for name,value in expected.items()), 'Final source pins differ from recovery receipt'
    pilot_before=pilot_hashes()
    (output/'pilot_frozen_before.json').write_text(json.dumps(pilot_before,sort_keys=True,indent=2)+'\n')
    (output/'source_label.txt').write_text(args.source_label+'\n')
    run('tool_versions', ['bash', '-c', 'hostname; uname -a; rustc -vV; cargo -vV; cc --version; protoc --version'])
    build_ok = run('make_build', ['make', 'build', 'CARGO=cargo --locked --offline']) == 0

    fixture = ROOT/'target/debug/contextstore-server'
    fixture_kind = 'prior_default_build_verified_by_sha256'
    if not fixture.is_file() or digest(fixture) != DEFAULT_FIXTURE_SHA:
        fixture_env = env.copy()
        fixture_env['CARGO_TARGET_DIR'] = str(ROOT/'target-default-fixture')
        rc = run('default_fixture_build', ['cargo','build','--locked','--offline','-j','4','-p','contextstore-server','--bin','contextstore-server'], fixture_env, ROOT/'src-baseline')
        if rc != 0:
            raise RuntimeError('Default-feature E2E fixture build failed; inspect preserved attempt')
        fixture = ROOT/'target-default-fixture/debug/contextstore-server'
        fixture_kind = 'fresh_default_feature_build_from_frozen_src_baseline'
    (output/'default_fixture.json').write_text(json.dumps({'path': str(fixture), 'sha256': digest(fixture), 'provenance': fixture_kind, 'source_commit':'3fe4b26d72fffbe3a3d95236b858ab2ed0d78a6b'}, indent=2)+'\n')

    redis_dir = output/'redis'
    redis_dir.mkdir()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0))
        port = reservation.getsockname()[1]
    with (redis_dir/'server.log').open('wb') as redis_log:
        redis = subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(port),'--save','','--appendonly','no','--dir',str(redis_dir)], env=env, stdout=redis_log, stderr=subprocess.STDOUT)
        try:
            for attempt in range(100):
                if redis.poll() is not None:
                    raise RuntimeError('Task-owned Redis exited during startup')
                try:
                    with socket.create_connection(('127.0.0.1',port),0.2) as connection:
                        connection.sendall(b'*1\r\n$4\r\nPING\r\n')
                        if connection.recv(32).startswith(b'+PONG'):
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError('Task-owned Redis readiness timeout')
            test_env = env.copy()
            test_env.update(CS_REDIS_URL='redis://127.0.0.1:%d/' % port, CS_E2E_SERVER_BIN=str(fixture))
            (output/'redis_identity.json').write_text(json.dumps({'pid':redis.pid,'port':port,'directory':str(redis_dir),'readiness':'PONG'},indent=2)+'\n')
            run('make_test_server', ['make','test-server','CARGO=cargo --locked --offline'], test_env)
            run('workspace_default', ['cargo','test','--locked','--offline','-j','4','--workspace','--','--nocapture'], test_env)
            run('workspace_rdma', ['cargo','test','--locked','--offline','-j','4','--workspace','--features','rdma','--','--nocapture'], test_env)
        finally:
            redis.terminate()
            try:
                redis.wait(timeout=10)
            except subprocess.TimeoutExpired:
                redis.kill()
                redis.wait()
            (output/'redis_stopped.json').write_text(json.dumps({'pid':redis.pid,'exit_code':redis.returncode,'finished':now()},indent=2)+'\n')

    run('client_clippy', ['cargo','clippy','--locked','--offline','-j','4','-p','contextstore-client-rs','--all-targets','--features','rdma','--','-D','warnings'])
    changed = [line.strip() for line in args.changed_files.read_text().splitlines() if line.strip()]
    for name in changed:
        if not name.startswith('kv-service/client-rs/') or not name.endswith('.rs') or '..' in Path(name).parts:
            raise ValueError('Unexpected targeted formatting path: '+name)
    (output/'fmt_scope.json').write_text(json.dumps(changed,indent=2)+'\n')
    run('client_targeted_fmt', ['rustfmt','--check','--edition','2021','--config','skip_children=true'] + changed)

    ffi = output/'ffi-input'
    if build_ok:
        ffi.mkdir()
        for name in ['run_ffi_lifecycle_validation.py','ffi_lifecycle_probe.py','verbs_failstop_shim.c']:
            shutil.copy2(SOURCE/'kv-service/verification'/name, ffi/name)
        shutil.copy2(ROOT/'target-candidate/release/libcontextstore_rdma_ffi.so', ffi/'libcontextstore_rdma_ffi.so')
        run('ffi_shim_build', ['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-o',str(ffi/'verbs_failstop.so'),str(ffi/'verbs_failstop_shim.c'),'-ldl'])
        (output/'ffi_input_hashes.json').write_text(json.dumps({p.name:digest(p) for p in ffi.iterdir() if p.is_file()},sort_keys=True,indent=2)+'\n')
    after = source_hashes()
    (output/'source_after.json').write_text(json.dumps(after,sort_keys=True,indent=2)+'\n')
    pilot_after=pilot_hashes()
    (output/'pilot_frozen_after.json').write_text(json.dumps(pilot_after,sort_keys=True,indent=2)+'\n')
    binaries={}
    for name in ['contextstore-server','cs-meta','cs-rail-read-bench','libcontextstore_rdma_ffi.so']:
        path=ROOT/'target-candidate/release'/name
        if path.is_file():binaries[name]=digest(path)
    summary={'source_label':args.source_label,'source_files':len(before),'source_stable':before==after,'pilot_frozen_unchanged':pilot_before==pilot_after,'results':results,'release_binary_sha256':binaries,'native_ffi_status':'prepared_only; must run separately after recovery-agent coordination','all_host_checks_passed':before==after and pilot_before==pilot_after and all(r['exit_code']==0 for r in results.values()),'output':str(output)}
    (output/'host_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)
    raise SystemExit(0 if summary['all_host_checks_passed'] else 1)

if __name__=='__main__':
    main()
