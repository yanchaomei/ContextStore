import pathlib,os,subprocess,json,hashlib,datetime,socket,time,signal
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029');root=base/'wave2/final-integration';src=root/'src';ev=root/'evidence';manifest=json.loads((root/'source-input-manifest.json').read_text());freeze=json.loads((root/'local-source-freeze.json').read_text())
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def verify():return {name:hashlib.sha256((src/name).read_bytes()).hexdigest() for name in manifest}
expected={name:info['sha256'] for name,info in manifest.items()};before=verify();assert before==expected
(ev/'source-hashes-before.json').write_text(json.dumps(before,indent=2)+'\n')
env={k:v for k,v in os.environ.items() if not k.startswith('CS_')}
env.update({'PATH':str(base/'tools/rust/bin')+':/usr/local/bin:/usr/bin:/bin','CARGO_HOME':str(base/'tools/cargo'),'CARGO_TARGET_DIR':str(root/'target'),'CARGO_BUILD_JOBS':'4','CARGO_NET_OFFLINE':'true','LIBCLANG_PATH':'/usr/lib/x86_64-linux-gnu','PROTOC':'/usr/local/bin/protoc','TMPDIR':str(root/'tmp')})
fixture=base/'target-default-fixture/debug/contextstore-server';fixture_hash=hashlib.sha256(fixture.read_bytes()).hexdigest();assert fixture_hash=='43e7602578ff971aeab80bdd2d4e4591d0cd3b0aaea5a00e89cd148cd5294b4f'
env['CS_E2E_SERVER_BIN']=str(fixture)
versions={}
for name,cmd in [('rustc',['rustc','-Vv']),('cargo',['cargo','-V']),('rustfmt',['rustfmt','--version']),('clippy',['cargo','clippy','--version']),('protoc',[env['PROTOC'],'--version']),('kernel',['uname','-a'])]:
 p=subprocess.run(cmd,env=env,text=True,capture_output=True);versions[name]={'argv':cmd,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr};assert p.returncode==0
(ev/'toolchain.json').write_text(json.dumps(versions,indent=2)+'\n')
redis_dir=root/'redis';redis_dir.mkdir()
with socket.socket() as reservation:reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
redis_cmd=['redis-server','--bind','127.0.0.1','--port',str(port),'--save','','--appendonly','no','--dir',str(redis_dir)]
redis=subprocess.Popen(redis_cmd,env=env,cwd=root,stdout=open(ev/'redis.stdout','wb'),stderr=open(ev/'redis.stderr','wb'),stdin=subprocess.DEVNULL,start_new_session=True)
r={'state':'RUNNING','started':now(),'source_manifest_sha256':freeze['input_manifest_sha256'],'input_files':len(manifest),'source_matches_local_before':before==expected,'e2e_fixture':{'path':str(fixture),'sha256':fixture_hash},'target':str(root/'target'),'steps':[],'server_strict_clippy':'FAIL_WITH_UNCHANGED_BASELINE_DIAGNOSTICS inherited; default31/fullRDMA53unique, new0; not rerun','excluded_execution':['Blocked legacyFFI12','Any ignored hardware/RXE tests','Existing soak/recovery/frozen binaries'],'redis':{'pid':redis.pid,'port':port,'argv':redis_cmd}}
def save(): (ev/'integration-progress.json').write_text(json.dumps(r,indent=2)+'\n')
save()
try:
 deadline=time.monotonic()+15
 while time.monotonic()<deadline:
  assert redis.poll() is None,'privateRedis exited'
  try:
   with socket.create_connection(('127.0.0.1',port),.25) as s:
    s.sendall(b'*1\r\n$4\r\nPING\r\n')
    if s.recv(32).startswith(b'+PONG'):break
  except OSError:time.sleep(.05)
 else:raise RuntimeError('Private Redis readiness deadline')
 proc=pathlib.Path('/proc')/str(redis.pid);r['redis'].update({'start_ticks':(proc/'stat').read_text().split()[21],'exe':os.readlink(proc/'exe'),'exe_sha256':hashlib.sha256((proc/'exe').read_bytes()).hexdigest()})
 env['CS_REDIS_URL']=f'redis://127.0.0.1:{port}/'
 r['environment']={k:v for k,v in env.items() if k.startswith(('CARGO_','CS_')) or k in ['PATH','PROTOC','LIBCLANG_PATH','TMPDIR']};save()
 steps=[('changed_rustfmt',['rustfmt','--check','--edition','2021']+freeze['changed_rust'],300),('make_build_release',['make','build'],1800),('workspace_default',['cargo','test','--workspace','--locked','--offline','-j','4','--','--nocapture'],900),('workspace_rdma',['cargo','test','--workspace','--features','rdma','--locked','--offline','-j','4','--','--nocapture'],900),('client_clippy_default',['cargo','clippy','--locked','--offline','-j','4','-p','contextstore-client-rs','--all-targets','--','-D','warnings'],600),('client_clippy_rdma',['cargo','clippy','--locked','--offline','-j','4','-p','contextstore-client-rs','--all-targets','--features','rdma','--','-D','warnings'],600)]
 for label,cmd,limit in steps:
  record={'label':label,'argv':cmd,'cwd':str(src),'started':now(),'timeout_seconds':limit};r['current_step']=label
  (ev/f'{label}.command.json').write_text(json.dumps(record,indent=2)+'\n');save()
  with open(ev/f'{label}.stdout','wb') as out,open(ev/f'{label}.stderr','wb') as err:
   p=subprocess.Popen(cmd,cwd=src,env=env,stdout=out,stderr=err,stdin=subprocess.DEVNULL,start_new_session=True);record['pid']=p.pid;save()
   try:code=p.wait(timeout=limit)
   except subprocess.TimeoutExpired:
    os.killpg(p.pid,signal.SIGTERM)
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
    code=124;record['timeout']=True
  record.update({'exit_code':code,'finished':now()});r['steps'].append(record);(ev/f'{label}.exit_code').write_text(str(code)+'\n');(ev/f'{label}.receipt.json').write_text(json.dumps(record,indent=2)+'\n');save();print(label,code,flush=True)
 after=verify();(ev/'source-hashes-after.json').write_text(json.dumps(after,indent=2)+'\n');r['source_matches_local_after']=after==expected
 r['fixture_unchanged']=hashlib.sha256(fixture.read_bytes()).hexdigest()==fixture_hash
 r['release_artifact_sha256']={str(p.relative_to(root/'target')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'target/release').iterdir() if p.is_file() and p.name in ['contextstore-server','cs-meta','cs-kvservice-bench','cs-bench','cs-rdma-bench','cs-rail-read-bench','libcontextstore_rdma_ffi.so']}
 r['state']='INTEGRATION_CHECKS_PASS_WITH_INHERITED_SERVER_CLIPPY_FAILURE' if all(x['exit_code']==0 for x in r['steps']) and r['source_matches_local_after'] and r['fixture_unchanged'] else 'INTEGRATION_CHECKS_HAVE_FAILURES'
except BaseException as exc:
 r['state']='RUNNER_FAILURE';r['exception']=repr(exc);raise
finally:
 if redis.poll() is None:
  proc=pathlib.Path('/proc')/str(redis.pid)
  assert (proc/'stat').read_text().split()[21]==r['redis']['start_ticks']
  stop=subprocess.run(['redis-cli','-h','127.0.0.1','-p',str(port),'SHUTDOWN','SAVE'],env=env,text=True,capture_output=True)
  (ev/'redis-shutdown.stdout').write_text(stop.stdout);(ev/'redis-shutdown.stderr').write_text(stop.stderr);r['redis']['shutdown_exit']=stop.returncode
  redis.wait(timeout=15)
 r['redis']['exit_code']=redis.returncode;r['redis']['stopped']=redis.poll() is not None
 r['finished']=now();r['current_step']=None;save();(ev/'integration-receipt.json').write_text(json.dumps(r,indent=2)+'\n')
print(r['state'],flush=True)
