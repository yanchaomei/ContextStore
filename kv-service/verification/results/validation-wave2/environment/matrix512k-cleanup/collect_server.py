import pathlib,os,json,hashlib,datetime,tarfile,socket,subprocess
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029');root=base/'matrix512k';out=base/'matrix512k-cleanup-20261029';out.mkdir()
expected={'server':(1882561,'313266078','0e9c081f15f809839684bab64e24b75c547ec1ce4e0e1a4a2060f6146c76c87c',root),'redis':(1881946,'313266067','22ec037f0a20dc580fbd5f9958cd53b70d352b5697201f3391d660cdd266321c',root/'redis')}
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hostname':socket.gethostname(),'guards':{},'files':{}}
for label,(pid,ticks,digest,cwd) in expected.items():
 p=pathlib.Path('/proc')/str(pid)
 assert int((root/'evidence'/f'{label}.pid').read_text())==pid
 args=[s.decode() for s in (p/'cmdline').read_bytes().split(b'\0') if s]
 env=dict(x.split('=',1) for x in (p/'environ').read_text().split('\0') if x)
 actual={'pid':pid,'start_ticks':(p/'stat').read_text().split()[21],'exe':os.readlink(p/'exe'),'exe_sha256':hashlib.sha256((p/'exe').read_bytes()).hexdigest(),'cwd':os.readlink(p/'cwd'),'argv':args,'environment':env,'environment_sha256':hashlib.sha256((p/'environ').read_bytes()).hexdigest()}
 assert actual['start_ticks']==ticks and actual['exe_sha256']==digest and actual['cwd']==str(cwd),(label,actual)
 if label=='server':
  assert args==[str(base/'bin/contextstore-server'),'--config',str(root/'server.toml'),'--log-level','info']
  assert env['CS_RDMA_SLAB_MB']=='512' and env['CS_FORCE_DISK_READ']=='1' and env['CS_RDMA_DEVICES']=='mlx5_1:10.0.0.65:25553:3'
 r['guards'][label]=actual
assert hashlib.sha256((root/'server.toml').read_bytes()).hexdigest()=='3e6187f88ee08c5664bd838e4034b2dc100ee43a129eefb3261e4df133c77380'
cli=[str(base/'bin/redis-cli'),'-p','26579'];env={**os.environ,'LD_LIBRARY_PATH':str(base/'lib')}
save=subprocess.run(cli+['SAVE'],env=env,text=True,capture_output=True);assert save.returncode==0 and save.stdout.strip()=='OK';r['redis_save_ack']=save.stdout.strip()
for parent in [root/'evidence',root/'redis',base/'bin',base/'lib']:
 for p in sorted(parent.rglob('*')):
  if p.is_file():r['files'][str(p.relative_to(base))]={'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
p=root/'server.toml';r['files'][str(p.relative_to(base))]={'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
data=[p for p in (root/'data').rglob('*') if p.is_file()];r['data_preserved_inventory']={'file_count':len(data),'bytes':sum(p.stat().st_size for p in data),'root':str(root/'data')}
r['tcp_listeners']=subprocess.check_output(['ss','-lntp'],text=True)
with tarfile.open(out/'server-prestop-evidence-and-snapshot.tar.gz','w:gz') as t:
 for name in ['evidence','server.toml','redis']:t.add(root/name,arcname='matrix512k/'+name)
archive=out/'server-prestop-evidence-and-snapshot.tar.gz';r['archive_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest();r['archive_size']=archive.stat().st_size
(out/'server-prestop-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
