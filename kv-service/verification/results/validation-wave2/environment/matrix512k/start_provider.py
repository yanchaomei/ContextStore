import pathlib,hashlib,socket,subprocess,json,datetime,os,time
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029'); root=base/'matrix512k'
(root/'evidence').mkdir(parents=True,exist_ok=True)
assert not (root/'evidence/server.pid').exists()
binary=base/'bin/contextstore-server';expected='0e9c081f15f809839684bab64e24b75c547ec1ce4e0e1a4a2060f6146c76c87c'
assert hashlib.sha256(binary.read_bytes()).hexdigest()==expected
assert hashlib.sha256((base/'bin/redis-server').read_bytes()).hexdigest()=='22ec037f0a20dc580fbd5f9958cd53b70d352b5697201f3391d660cdd266321c'
env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C'}
ldd={}
for name in ['contextstore-server','redis-server']:
 e={**env,**({'LD_LIBRARY_PATH':str(base/'lib')} if name=='redis-server' else {})}
 p=subprocess.run(['ldd',str(base/'bin'/name)],env=e,text=True,capture_output=True)
 ldd[name]={'code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
 assert p.returncode==0 and 'not found' not in p.stdout,(name,p.stdout)
sockets=[]
for address,port in [('10.0.0.65',25551),('10.0.0.65',25553),('127.0.0.1',26579)]:
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind((address,port));sockets.append(s)
for part in ['data/stripe0','data/stripe1','redis']:(root/part).mkdir(parents=True,exist_ok=True)
for s in sockets:s.close()
processes={}
for label,command,extra in [
 ('redis',[str(base/'bin/redis-server'),'--bind','127.0.0.1','--port','26579','--save','','--appendonly','no','--dir',str(root/'redis')],{'LD_LIBRARY_PATH':str(base/'lib')}),
 ('server',[str(binary),'--config',str(root/'server.toml'),'--log-level','info'],{'CS_RDMA_SLAB_MB':'512','CS_FORCE_DISK_READ':'1','CS_RDMA_DEVICES':'mlx5_1:10.0.0.65:25553:3'})]:
 p=subprocess.Popen(command,cwd=root,env={**env,**extra},stdout=open(root/'evidence'/f'{label}.log','wb'),stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
 (root/'evidence'/f'{label}.pid').write_text(str(p.pid)+'\n')
 deadline=time.monotonic()+20
 while time.monotonic()<deadline:
  if p.poll() is not None:raise RuntimeError((label,p.returncode))
  try:
   with socket.create_connection(('127.0.0.1',26579) if label=='redis' else ('10.0.0.65',25551),timeout=.25):break
  except OSError:time.sleep(.1)
 else:raise RuntimeError(f'{label} not listening')
 proc=pathlib.Path('/proc')/str(p.pid);actualenv=dict(x.split('=',1) for x in (proc/'environ').read_text().split('\0') if x)
 processes[label]={'pid':p.pid,'start_ticks':(proc/'stat').read_text().split()[21],'command':command,'cwd':os.readlink(proc/'cwd'),'environment':actualenv,'environment_sha256':hashlib.sha256((proc/'environ').read_bytes()).hexdigest(),'exe':os.readlink(proc/'exe'),'exe_sha256':hashlib.sha256((proc/'exe').read_bytes()).hexdigest()}
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'root':str(root),'source_commit':'3fe4b26d72fffbe3a3d95236b858ab2ed0d78a6b','server_sha256':expected,'config_sha256':hashlib.sha256((root/'server.toml').read_bytes()).hexdigest(),'processes':processes,'ldd':ldd,'kernel':subprocess.check_output(['uname','-a'],text=True),'rdma':subprocess.check_output(['rdma','link','show'],text=True),'private_lib_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (base/'lib').glob('*')},'scope':'Independent SKV6-client to SKV5-server single-HCA fixture; one RDMA listener; not dual HCA'}
(root/'evidence/startup-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
