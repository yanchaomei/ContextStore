import pathlib,hashlib,socket,subprocess,json,datetime,os,time
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_provider_20261029')
root=base/'wave2'
assert not (root/'evidence/server.pid').exists() and not (root/'evidence/redis.pid').exists(), 'Refuse overwrite started wave2 provider'
expected='0e9c081f15f809839684bab64e24b75c547ec1ce4e0e1a4a2060f6146c76c87c'
binary=base/'bin/contextstore-server'
assert hashlib.sha256(binary.read_bytes()).hexdigest()==expected
sockets=[]
for address,port in [('10.0.0.64',25351),('10.0.0.64',25353),('10.0.0.64',25354),('127.0.0.1',26379),('127.0.0.1',25390)]:
 s=socket.socket(); s.bind((address,port)); sockets.append(s)
for part in ['evidence','data/stripe0','data/stripe1','redis']: (root/part).mkdir(parents=True,exist_ok=True)
config=(base/'server.toml').read_text().replace(str(base/'data'),str(root/'data')).replace('wave1-provider-node4','wave2-provider-node4').replace('contextstore:wave1-provider-20261029:','contextstore:wave2-provider-20261029:')
(root/'server.toml').write_text(config)
for s in sockets:s.close()
env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C'}
processes={}
for label,command,extra in [
 ('redis',['redis-server','--bind','127.0.0.1','--port','26379','--save','','--appendonly','no','--dir',str(root/'redis')],{}),
 ('server',[str(binary),'--config',str(root/'server.toml'),'--log-level','info'],{'CS_RDMA_SLAB_MB':'64','CS_FORCE_DISK_READ':'1','CS_RDMA_DEVICES':'mlx5_1:10.0.0.64:25353:3,mlx5_1:10.0.0.64:25354:3'})]:
 log=open(root/'evidence'/f'{label}.log','wb')
 p=subprocess.Popen(command,cwd=root,env={**env,**extra},stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
 (root/'evidence'/f'{label}.pid').write_text(str(p.pid)+'\n')
 processes[label]={'pid':p.pid,'command':command,'environment':{**env,**extra}}
 deadline=time.monotonic()+20
 while time.monotonic()<deadline:
  if p.poll() is not None: raise RuntimeError((label,p.returncode))
  try:
   with socket.create_connection(('127.0.0.1',26379) if label=='redis' else ('10.0.0.64',25351),timeout=.25): break
  except OSError: time.sleep(.1)
 else: raise RuntimeError(f'{label} not listening')
 proc=pathlib.Path('/proc')/str(p.pid)
 processes[label].update({'start_ticks':(proc/'stat').read_text().split()[21],'exe':os.readlink(proc/'exe'),'exe_sha256':hashlib.sha256((proc/'exe').read_bytes()).hexdigest()})
receipt={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'root':str(root),'server_source_commit':'3fe4b26d72fffbe3a3d95236b858ab2ed0d78a6b','server_sha256':expected,'source_config_sha256':hashlib.sha256((base/'server.toml').read_bytes()).hexdigest(),'config_sha256':hashlib.sha256((root/'server.toml').read_bytes()).hexdigest(),'processes':processes,'kernel':subprocess.check_output(['uname','-a'],text=True),'topology':subprocess.check_output(['rdma','link','show'],text=True),'ip':subprocess.check_output(['ip','-br','addr'],text=True),'scope':'one physical HCA, two QP listeners; correctness only while RXE work concurrent'}
(root/'evidence/restore_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2))
