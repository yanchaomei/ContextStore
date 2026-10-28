#!/usr/bin/env python3
"""Separate stage: run only after parent GO and recovery-agent guest coordination."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT=Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029')
VM=ROOT/'vm-rxe'

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host-attempt',type=Path,required=True)
    args=parser.parse_args()
    host=args.host_attempt.resolve()
    source_label=(host/'source_label.txt').read_text().strip()
    expected=json.loads((host/'ffi_input_hashes.json').read_text())
    files=sorted((host/'ffi-input').iterdir())
    assert {p.name:digest(p) for p in files if p.is_file()}==expected
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output=host/('native-ffi-'+stamp)
    output.mkdir(exist_ok=False)
    guest='/home/railtest/cswave1/final-ffi/'+host.name+'-'+stamp
    ssh=['ssh','-oBatchMode=yes','-oStrictHostKeyChecking=yes','-oUserKnownHostsFile='+str(VM/'known_hosts'),'-i',str(VM/'railtest_ed25519'),'-p','22352','railtest@127.0.0.1']
    scp=['scp','-oBatchMode=yes','-oStrictHostKeyChecking=yes','-oUserKnownHostsFile='+str(VM/'known_hosts'),'-i',str(VM/'railtest_ed25519'),'-P','22352']
    stages={}

    def run(name,command):
        (output/(name+'.command.json')).write_text(json.dumps(command,indent=2)+'\n')
        with (output/(name+'.stdout')).open('wb') as stdout,(output/(name+'.stderr')).open('wb') as stderr:
            process=subprocess.run(command,stdout=stdout,stderr=stderr)
        (output/(name+'.exit_code')).write_text(str(process.returncode)+'\n')
        stages[name]=process.returncode
        return process.returncode

    assert run('create_guest_directory',ssh+['test ! -e '+shlex.quote(guest)+' && mkdir -p '+shlex.quote(guest)])==0
    assert run('copy_inputs',scp+[str(p) for p in files]+['railtest@127.0.0.1:'+guest+'/'])==0
    paths=' '.join(shlex.quote(guest+'/'+p.name) for p in files)
    assert run('guest_hashes',ssh+['sha256sum '+paths])==0
    actual={Path(line.split(None,1)[1].strip()).name:line.split()[0] for line in (output/'guest_hashes.stdout').read_text().splitlines()}
    assert actual==expected,(actual,expected)
    run('guest_environment',ssh+['hostname; uname -a; ulimit -l; rdma link show; python3 --version; ldd '+shlex.quote(guest+'/libcontextstore_rdma_ffi.so')+'; ldd '+shlex.quote(guest+'/verbs_failstop.so')])
    command=['env','-u','LD_PRELOAD','-u','PYTHONOPTIMIZE','python3',guest+'/run_ffi_lifecycle_validation.py',
             '--library',guest+'/libcontextstore_rdma_ffi.so','--shim',guest+'/verbs_failstop.so',
             '--device','rxe0','--gid','1','--output',guest+'/native','--source-commit',source_label,'--require-timeout']
    rc=run('native_ffi_13',ssh+[' '.join(shlex.quote(part) for part in command)])
    copy_rc=run('collect_native_receipt',scp+['-r','railtest@127.0.0.1:'+guest+'/native',str(output/'native')])
    result_path=output/'native/result.json'
    result=json.loads(result_path.read_text()) if result_path.exists() else None
    passed=rc==0 and copy_rc==0 and result is not None and result['all_passed'] and len(result['cases'])==13
    summary={'source_label':source_label,'host_attempt':str(host),'guest_directory':guest,'guest_role':'client',
             'device':'rxe0','gid_index':1,'stages':stages,'case_count':len(result['cases']) if result else None,
             'all_13_passed':passed,'input_hashes':expected,'scope':'real local Verbs allocations and self-QP control handshake; shim only in probe child processes; no server preload and no RDMA data-work-request claim'}
    (output/'native_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)
    raise SystemExit(0 if passed else 1)

if __name__=='__main__':
    main()
