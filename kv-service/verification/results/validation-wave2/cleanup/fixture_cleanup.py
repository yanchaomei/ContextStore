import pathlib,subprocess,hashlib,json,os,socket,datetime,tarfile,time,signal,sys
phase=sys.argv[1];out=pathlib.Path(CONFIG['out']);out.mkdir(exist_ok=True);stage=phase
assert socket.gethostname()==CONFIG['hostname']
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def guard(f,label):
 g=f['processes'][label];pid=g['pid'];p=pathlib.Path('/proc')/str(pid)
 actual={'pid':pid,'start_ticks':(p/'stat').read_text().split()[21],'exe':os.readlink(p/'exe'),'exe_sha256':sha(p/'exe'),'cwd':os.readlink(p/'cwd'),'argv':[x.decode() for x in (p/'cmdline').read_bytes().split(b'\0') if x]}
 assert actual['start_ticks']==g['start_ticks'] and actual['exe']==g['exe'] and actual['exe_sha256']==g['exe_sha256'],(label,actual)
 expectedcwd=f['root']+('/redis' if label=='redis' else '')
 assert actual['cwd']==expectedcwd,(label,actual)
 if label=='server':
  assert actual['argv'][actual['argv'].index('--config')+1]==f['root']+'/server.toml'
  assert sha(pathlib.Path(f['root'])/'server.toml')==f['config_sha256']
  env=dict(x.split('=',1) for x in (p/'environ').read_text().split('\0') if x);actual['CS_environment']={k:v for k,v in env.items() if k.startswith('CS_')}
  assert actual['CS_environment']=={k:v for k,v in g['environment'].items() if k.startswith('CS_')}
 return actual
def redis(f,command):
 env={**os.environ}
 if f.get('redis_library_path'):env['LD_LIBRARY_PATH']=f['redis_library_path']
 p=subprocess.run([f['redis_cli'],'-h','127.0.0.1','-p',str(f['redis_port'])]+command,env=env,text=True,capture_output=True)
 assert p.returncode==0,(command,p.returncode,p.stdout,p.stderr)
 return p.stdout.strip()
def check_native():
 if not CONFIG.get('native_guard_readonly'):return None
 lines=subprocess.check_output(['ss','-H','-lntp'],text=True).splitlines();assert not [x for x in lines if any(':'+str(p)+' ' in x for p in [55451,55453,6396])]
 active=[]
 for p in pathlib.Path('/proc').glob('[0-9]*'):
  try:exe=os.readlink(p/'exe')
  except (FileNotFoundError,PermissionError,ProcessLookupError):continue
  if '/home/railtest/wave2-descriptor-guard/' in exe:active.append(int(p.name))
 assert not active
 return {'owned_native_guard_processes':active,'private_native_guard_ports_free':True,'action':'read-only confirmation; no process signals'}
def archive(label):
 manifests={};artifacts={};data={}
 candidates=[]
 for name in CONFIG['archive_roots']:
  base=pathlib.Path(name)
  if not base.exists():continue
  for p in base.rglob('*'):
   if not p.is_file():continue
   rel=p.relative_to(base)
   if any(part=='data' for part in rel.parts):
    data[str(p)]={'size':p.stat().st_size};continue
   binary=any(part in ['bin','native-frozen-bin','target'] for part in rel.parts)
   if not binary:
    with p.open('rb') as f:binary=f.read(4)==b'\x7fELF'
   if binary:
    artifacts[str(p)]={'size':p.stat().st_size,'sha256':sha(p)};continue
   candidates.append(p)
 for extra in CONFIG.get('hash_only',[]):
  p=pathlib.Path(extra)
  if p.is_file():artifacts[str(p)]={'size':p.stat().st_size,'sha256':sha(p)}
 path=out/f'{label}-logs-config-snapshots.tar.gz'
 with tarfile.open(path,'w:gz') as t:
  for p in sorted(set(candidates)):
   name=str(p.relative_to('/'));manifests[name]={'size':p.stat().st_size,'sha256':sha(p)};t.add(p,arcname=name,recursive=False)
 return {'archive_path':str(path),'archive_sha256':sha(path),'archive_bytes':path.stat().st_size,'archived_files':manifests,'artifact_hashes':artifacts,'preserved_data_files':len(data),'preserved_data_bytes':sum(x['size'] for x in data.values())}
if phase=='collect':
 assert not (out/'prestop-receipt.json').exists()
 r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hostname':socket.gethostname(),'guards':{},'native_guard':check_native()}
 for f in CONFIG['fixtures']:
  r['guards'][f['root']]={label:guard(f,label) for label in ['server','redis']}
  assert redis(f,['SAVE'])=='OK'
 r['collection']=archive('prestop');(out/'prestop-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
elif phase=='stop':
 prior=json.loads((out/'prestop-receipt.json').read_text());r={'started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'stops':{}}
 for f in CONFIG['fixtures']:
  for label in ['server','redis']:
   actual=guard(f,label);assert actual==prior['guards'][f['root']][label];pid=actual['pid'];p=pathlib.Path('/proc')/str(pid)
   if label=='server':os.kill(pid,signal.SIGTERM);action='SIGTERM'
   else:redis(f,['SHUTDOWN','SAVE']);action='SHUTDOWN SAVE'
   deadline=time.monotonic()+20;state='running'
   while time.monotonic()<deadline:
    if not p.exists():state='absent';break
    stat=(p/'stat').read_text().split()
    if stat[21]!=actual['start_ticks']:state='original_exited_pid_reused';break
    if stat[2]=='Z':state='exited_zombie';break
    time.sleep(.1)
   assert state!='running';r['stops'][str(pid)]={'root':f['root'],'label':label,'action':action,'state':state,'start_ticks':actual['start_ticks']}
   (out/'stop-progress.json').write_text(json.dumps(r,indent=2)+'\n')
 lines=subprocess.check_output(['ss','-H','-lntp'],text=True).splitlines();ports=[p for f in CONFIG['fixtures'] for p in f['ports']];matching=[x for x in lines if any(':'+str(p)+' ' in x for p in ports)];assert not matching
 r['task_listeners_after']=matching;r['collection']=archive('poststop')
 assert r['collection']['preserved_data_files']==prior['collection']['preserved_data_files'] and r['collection']['preserved_data_bytes']==prior['collection']['preserved_data_bytes']
 r['finished']=datetime.datetime.now(datetime.timezone.utc).isoformat();(out/'stop-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
elif phase=='finalize':
 prior=json.loads((out/'prestop-receipt.json').read_text());r=json.loads((out/'stop-progress.json').read_text())
 assert set(r['stops'])=={str(f['processes'][k]['pid']) for f in CONFIG['fixtures'] for k in ['server','redis']}
 for pid in r['stops']:
  p=pathlib.Path('/proc')/pid
  assert not p.exists() or (p/'stat').read_text().split()[2]=='Z'
 ports=[p for f in CONFIG['fixtures'] for p in f['ports']];lines=subprocess.check_output(['ss','-H','-lntp'],text=True).splitlines();matching=[x for x in lines if any(':'+str(p)+' ' in x for p in ports)];assert not matching,matching
 r['task_listeners_after']=matching;r['collection']=archive('poststop')
 assert r['collection']['preserved_data_files']==prior['collection']['preserved_data_files'] and r['collection']['preserved_data_bytes']==prior['collection']['preserved_data_bytes']
 r['finalize_note']='Earlier stop completed both signals; immediate port assertion failed. Finalization is readback/archive only; no repeated signals.'
 r['finished']=datetime.datetime.now(datetime.timezone.utc).isoformat();(out/'stop-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
else:raise ValueError(phase)
