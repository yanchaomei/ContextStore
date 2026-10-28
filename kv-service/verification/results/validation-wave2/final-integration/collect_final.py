import pathlib,json,hashlib,subprocess,socket,tarfile,datetime,os
root=pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/wave2/final-integration');ev=root/'evidence';r=json.loads((ev/'integration-receipt.json').read_text());manifest=json.loads((root/'source-input-manifest.json').read_text())
assert len(r['steps'])==6 and r['redis']['stopped']
source={name:hashlib.sha256((root/'src'/name).read_bytes()).hexdigest() for name in manifest};assert source=={name:entry['sha256'] for name,entry in manifest.items()}
pid=r['redis']['pid'];p=pathlib.Path('/proc')/str(pid);assert not p.exists()
listeners=subprocess.check_output(['ss','-H','-lntp'],text=True).splitlines();matching=[x for x in listeners if ':'+str(r['redis']['port'])+' ' in x];assert not matching,matching
versions={str(p.relative_to(root/'target')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'target/release').iterdir() if p.is_file() and p.name in ['contextstore-server','cs-meta','cs-kvservice-bench','cs-bench','cs-rdma-bench','cs-rail-read-bench','libcontextstore_rdma_ffi.so']};assert versions==r['release_artifact_sha256']
final={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'input_hashes':source,'input_files':len(source),'all_source_matches_initial_local':True,'private_redis_pid_absent':True,'private_redis_port_listeners':matching,'release_artifact_sha256_matches_completion':True,'runner_sha256':hashlib.sha256((root/'run_integration.py').read_bytes()).hexdigest(),'kernel':subprocess.check_output(['uname','-a'],text=True)}
(ev/'final-readback.json').write_text(json.dumps(final,indent=2)+'\n')
files={str(p.relative_to(root)):{'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in ev.rglob('*') if p.is_file()}
for name in ['run_integration.py','source-input-manifest.json','local-source-freeze.json']:
 p=root/name;files[name]={'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
(root/'collected-file-hashes.json').write_text(json.dumps(files,indent=2)+'\n')
with tarfile.open(root/'final-integration-evidence.tar.gz','w:gz') as t:
 for name in ['evidence','run_integration.py','source-input-manifest.json','local-source-freeze.json','collected-file-hashes.json']:t.add(root/name,arcname=name)
p=root/'final-integration-evidence.tar.gz';print(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'archive_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'archive_size':p.stat().st_size,'evidence_files':len(files),'state':r['state']},indent=2))
