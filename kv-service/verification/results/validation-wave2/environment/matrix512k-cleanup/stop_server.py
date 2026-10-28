import pathlib,os,signal,subprocess,time,json,hashlib,tarfile,datetime
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029');root=base/'matrix512k';out=base/'matrix512k-cleanup-20261029'
assert not (out/'server-stop-receipt.json').exists()
prior=json.loads((out/'server-prestop-receipt.json').read_text());r={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'stops':{}}
for label in ['server','redis']:
 guard=prior['guards'][label];pid=guard['pid'];proc=pathlib.Path('/proc')/str(pid)
 if label=='server' and not proc.exists():
  r['stops'][label]={'pid':pid,'start_ticks':guard['start_ticks'],'action':'SIGTERM in prior attempt','final_state':'absent','note':'Prior attempt stopped server, then refused Redis raw cmdline padding; no second server signal'}
  continue
 assert (proc/'stat').read_text().split()[21]==guard['start_ticks']
 assert hashlib.sha256((proc/'exe').read_bytes()).hexdigest()==guard['exe_sha256']
 assert os.readlink(proc/'cwd')==guard['cwd']
 assert [x for x in (proc/'cmdline').read_bytes().split(b'\0') if x]==[x.encode() for x in guard['argv']]
 if label=='server':
  assert hashlib.sha256((root/'server.toml').read_bytes()).hexdigest()=='3e6187f88ee08c5664bd838e4034b2dc100ee43a129eefb3261e4df133c77380'
  os.kill(pid,signal.SIGTERM);action='SIGTERM'
 else:
  env={**os.environ,'LD_LIBRARY_PATH':str(base/'lib')}
  cp=subprocess.run([str(base/'bin/redis-cli'),'-p','26579','SHUTDOWN','SAVE'],env=env,text=True,capture_output=True)
  (out/'redis-shutdown.stdout').write_text(cp.stdout);(out/'redis-shutdown.stderr').write_text(cp.stderr);assert cp.returncode==0,(cp.returncode,cp.stdout,cp.stderr);action='SHUTDOWN SAVE'
 deadline=time.monotonic()+20;state='running'
 while time.monotonic()<deadline:
  if not proc.exists():state='absent';break
  stat=(proc/'stat').read_text().split()
  if stat[21]!=guard['start_ticks']:state='original_exited_pid_reused';break
  if stat[2]=='Z':state='exited_zombie';break
  time.sleep(.1)
 assert state!='running',(label,pid)
 r['stops'][label]={'pid':pid,'start_ticks':guard['start_ticks'],'action':action,'final_state':state}
 (out/'stop-progress.json').write_text(json.dumps(r,indent=2)+'\n')
listeners=subprocess.check_output(['ss','-H','-lntp'],text=True)
matching=[line for line in listeners.splitlines() if any(':'+str(p)+' ' in line for p in [25551,25553,26579])];assert not matching,matching
r['task_tcp_listeners_after']=matching
r['preserved_paths']={str(p):p.exists() for p in [root,root/'data',root/'redis/dump.rdb',root/'server.toml',root/'evidence/server.log',base/'bin/contextstore-server',base/'lib']};assert all(r['preserved_paths'].values())
r['snapshot_sha256']=hashlib.sha256((root/'redis/dump.rdb').read_bytes()).hexdigest()
r['server_log_sha256']=hashlib.sha256((root/'evidence/server.log').read_bytes()).hexdigest();r['server_log_bytes']=(root/'evidence/server.log').stat().st_size
files=[p for p in (root/'data').rglob('*') if p.is_file()];r['data_file_count']=len(files);r['data_bytes']=sum(p.stat().st_size for p in files)
assert r['data_file_count']==prior['data_preserved_inventory']['file_count'] and r['data_bytes']==prior['data_preserved_inventory']['bytes']
with tarfile.open(out/'server-poststop-evidence-and-snapshot.tar.gz','w:gz') as t:
 for name in ['evidence','server.toml','redis']:t.add(root/name,arcname='matrix512k/'+name)
archive=out/'server-poststop-evidence-and-snapshot.tar.gz';r['archive_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest();r['archive_size']=archive.stat().st_size
r['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
r['scope']='Only SKV5 matrix512k owned server and Redis stopped. All directories/data preserved. No SKV2/SKV4/RXE/source/build actions.'
(out/'server-stop-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
