from pathlib import Path
import subprocess,json,hashlib,tarfile,datetime
repo=Path('work/ContextStore-runtime-wave1').resolve();out=Path(__file__).resolve().parent
selected=set()
for name in ['Cargo.toml','Cargo.lock','Makefile','README.md','kv-service/Makefile','rust-toolchain','rust-toolchain.toml','rustfmt.toml','.rustfmt.toml','clippy.toml','.clippy.toml']:
 p=repo/name
 if p.is_file():selected.add(p)
for dirname in ['.cargo','kv-service/client-rs','kv-service/server','kv-service/rdma-ffi','kv-service/vendor','kv-service/proto','kv-service/configs']:
 d=repo/dirname
 if not d.exists():continue
 for p in d.rglob('*'):
  if p.is_file() and not any(x in ['target','.git','__pycache__'] for x in p.relative_to(repo).parts):selected.add(p)
for name in ['run_recovery_campaign.py','audit_recovery_campaign.py','run_runtime_soak.py']:
 selected.add(repo/'kv-service/verification'/name)
inputs={str(p.relative_to(repo)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':p.stat().st_size} for p in sorted(selected)}
changed=set(subprocess.check_output(['git','diff','--name-only','HEAD'],cwd=repo,text=True).splitlines())
untracked=set(subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=repo,text=True).splitlines())
changed_rust=sorted(x for x in inputs if x.endswith('.rs') and x in changed|untracked)
with tarfile.open(out/'source-inputs.tar.gz','w:gz') as t:
 for p in sorted(selected):t.add(p,arcname=str(p.relative_to(repo)),recursive=False)
for name,info in inputs.items():assert hashlib.sha256((repo/name).read_bytes()).hexdigest()==info['sha256']
(out/'source-input-manifest.json').write_text(json.dumps(inputs,indent=2)+'\n')
freeze={'state':'FROZEN_FROM_CURRENT_WORKING_FILES_NOT_GIT_ARCHIVE','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'repo':str(repo),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),'status':subprocess.check_output(['git','status','--short'],cwd=repo,text=True),'included_files':len(inputs),'included_bytes':sum(x['size'] for x in inputs.values()),'input_manifest_sha256':hashlib.sha256((out/'source-input-manifest.json').read_bytes()).hexdigest(),'source_archive_sha256':hashlib.sha256((out/'source-inputs.tar.gz').read_bytes()).hexdigest(),'untracked_included':sorted(set(inputs)&untracked),'changed_rust':changed_rust,'scope':'Root workspace cargo/lock and Makefiles; all3workspace crates and vendor/proto/configs/.cargo; current README; current verification runners/auditor. Existing results/media excluded. No local compilation/tests.'}
(out/'local-source-freeze.json').write_text(json.dumps(freeze,indent=2)+'\n');print(json.dumps(freeze,indent=2))
