import pathlib,os,subprocess,signal,time,json,hashlib,datetime,tarfile
root=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029');out=root/'matrix512k-transition';out.mkdir(exist_ok=True); assert not (out/'stop-receipt.json').exists()
expected={'server':(3940563,'313133088','0e9c081f15f809839684bab64e24b75c547ec1ce4e0e1a4a2060f6146c76c87c'),'redis':(3940507,'313133078','22ec037f0a20dc580fbd5f9958cd53b70d352b5697201f3391d660cdd266321c')}
receipt={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'guards':{},'stops':{}}
for label,(pid,ticks,digest) in expected.items():
 p=pathlib.Path('/proc')/str(pid)
 actual={'start_ticks':(p/'stat').read_text().split()[21],'exe_sha256':hashlib.sha256((p/'exe').read_bytes()).hexdigest(),'cwd':os.readlink(p/'cwd'),'command':(p/'cmdline').read_text().replace('\0',' ').strip()}
 assert actual['start_ticks']==ticks and actual['exe_sha256']==digest and actual['cwd']==str(root/'redis' if label=='redis' else root),(label,actual)
 receipt['guards'][label]={'pid':pid,**actual}
with tarfile.open(out/'prestop-baseline-evidence.tar.gz','w:gz') as t:
 for name in ['evidence','server.toml']:t.add(root/name,arcname=name)
(out/'identity-guards.json').write_text(json.dumps(receipt,indent=2)+'\n')
for label in ['server','redis']:
 pid,ticks,digest=expected[label];p=pathlib.Path('/proc')/str(pid)
 assert (p/'stat').read_text().split()[21]==ticks
 if label=='server':os.kill(pid,signal.SIGTERM);command='SIGTERM'
 else:
  env={**os.environ,'LD_LIBRARY_PATH':str(root/'lib')}
  cp=subprocess.run([str(root/'bin/redis-cli'),'-p','26579','SHUTDOWN','SAVE'],env=env,text=True,capture_output=True)
  (out/'redis-shutdown.stdout').write_text(cp.stdout);(out/'redis-shutdown.stderr').write_text(cp.stderr);assert cp.returncode==0
  command='redis-cli SHUTDOWN SAVE'
 deadline=time.monotonic()+20
 while p.exists() and time.monotonic()<deadline:
  if (p/'stat').read_text().split()[2]=='Z':break
  time.sleep(.1)
 stopped=not p.exists() or (p/'stat').read_text().split()[2]=='Z';assert stopped,(label,pid)
 receipt['stops'][label]={'pid':pid,'command':command,'stopped':stopped}
with tarfile.open(out/'poststop-baseline-evidence.tar.gz','w:gz') as t:
 for name in ['evidence','server.toml','redis']:t.add(root/name,arcname=name)
receipt['preserved_snapshot_sha256']=hashlib.sha256((root/'redis/dump.rdb').read_bytes()).hexdigest()
receipt['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
(out/'stop-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
