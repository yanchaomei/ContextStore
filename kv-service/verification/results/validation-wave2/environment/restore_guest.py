import pathlib,subprocess,json,hashlib,socket,datetime,os,time
base=pathlib.Path('/home/railtest/cswave1'); root=base/'wave2'
hostname=socket.gethostname(); assert hostname in ['csw1-rxe-server','csw1-rxe-client']
role=hostname.rsplit('-',1)[-1]
assert not root.exists(), 'Refuse overwrite wave2 guest'
(root/'evidence').mkdir(parents=True)
subprocess.run(['sudo','-n','modprobe','rdma_rxe'],check=True)
for dev,net in [('rxe0','rail0'),('rxe1','rail1')]:
 if not (pathlib.Path('/sys/class/infiniband')/dev).exists():subprocess.run(['sudo','-n','rdma','link','add',dev,'type','rxe','netdev',net],check=True)
receipt={'hostname':hostname,'role':role,'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'kernel':subprocess.check_output(['uname','-a'],text=True),'rdma':subprocess.check_output(['rdma','link','show'],text=True),'ip':subprocess.check_output(['ip','-br','addr'],text=True),'packages':subprocess.check_output(['dpkg-query','-W','rdma-core','ibverbs-providers','ibverbs-utils','iproute2','redis-server'],text=True),'binaries':{}}
expected={'contextstore-server':'0e9c081f15f809839684bab64e24b75c547ec1ce4e0e1a4a2060f6146c76c87c'} if role=='server' else {'rdma_e2e':'a458392eeaff6e5b9d64a9fcec2f08fb1832fa16253be213d79c1f9da6fa5957','rail_read_e2e':'06a2a6657c14b1099dead47a8dcb120efaa9676c880f09a302c483a813a0ee26'}
for name,digest in expected.items():
 path=base/'bin'/name; actual=hashlib.sha256(path.read_bytes()).hexdigest();assert actual==digest,(name,actual);receipt['binaries'][name]=actual
receipt['gids']={dev:{str(i):(pathlib.Path('/sys/class/infiniband')/dev/'ports/1/gids'/str(i)).read_text().strip() for i in [0,1]} for dev in ['rxe0','rxe1']}
if role=='server':
 sockets=[]
 for address,port in [('10.81.1.1',25351),('10.81.1.1',25353),('10.82.1.1',25354),('127.0.0.1',26379)]:
  s=socket.socket();s.bind((address,port));sockets.append(s)
 for part in ['data/stripe0','data/stripe1','redis']: (root/part).mkdir(parents=True)
 config=(base/'server.toml').read_text().replace(str(base/'data'),str(root/'data')).replace('wave1-rxe-server','wave2-rxe-server').replace('contextstore:wave1-rxe-20261029:','contextstore:wave2-rxe-20261029:')
 (root/'server.toml').write_text(config)
 for s in sockets:s.close()
 env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C'};receipt['processes']={}
 for label,command,extra in [
  ('redis',['redis-server','--bind','127.0.0.1','--port','26379','--save','','--appendonly','no','--dir',str(root/'redis')],{}),
  ('server',[str(base/'bin/contextstore-server'),'--config',str(root/'server.toml'),'--log-level','info'],{'CS_RDMA_SLAB_MB':'64','CS_FORCE_DISK_READ':'1','CS_RDMA_DEVICES':'rxe0:10.81.1.1:25353:1,rxe1:10.82.1.1:25354:1'})]:
  log=open(root/'evidence'/f'{label}.log','wb');e={**env,**extra}
  p=subprocess.Popen(command,cwd=root,env=e,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
  (root/'evidence'/f'{label}.pid').write_text(str(p.pid)+'\n')
  deadline=time.monotonic()+20
  while time.monotonic()<deadline:
   if p.poll() is not None:raise RuntimeError((label,p.returncode))
   try:
    with socket.create_connection(('127.0.0.1',26379) if label=='redis' else ('10.81.1.1',25351),timeout=.25):break
   except OSError:time.sleep(.1)
  else:raise RuntimeError(f'{label} not listening')
  proc=pathlib.Path('/proc')/str(p.pid)
  receipt['processes'][label]={'pid':p.pid,'start_ticks':(proc/'stat').read_text().split()[21],'command':command,'environment':e,'exe':os.readlink(proc/'exe'),'exe_sha256':hashlib.sha256((proc/'exe').read_bytes()).hexdigest()}
 receipt['config_sha256']=hashlib.sha256((root/'server.toml').read_bytes()).hexdigest()
else:
 (root/'client.env').write_bytes((base/'client.env').read_bytes())
 receipt['client_env_sha256']=hashlib.sha256((root/'client.env').read_bytes()).hexdigest()
(root/'evidence/restore_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
