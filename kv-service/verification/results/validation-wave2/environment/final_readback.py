import pathlib,subprocess,json,hashlib,socket,datetime,os
host=socket.gethostname()
roots=[pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_provider_20261029/wave2')] if host=='skv-node4' else [pathlib.Path('/home/railtest/cswave1/wave2'),pathlib.Path('/home/railtest/cswave1/wave2/small')]
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hostname':host,'fixtures':[]}
for root in roots:
 entry={'root':str(root),'hashes':{},'processes':{}}
 for name in ['server.toml','client.env']:
  p=root/name
  if p.exists():entry['hashes'][name]=hashlib.sha256(p.read_bytes()).hexdigest()
 for label in ['server','redis']:
  p=root/'evidence'/f'{label}.pid'
  if not p.exists():continue
  pid=int(p.read_text());proc=pathlib.Path('/proc')/str(pid)
  env=dict(x.split('=',1) for x in (proc/'environ').read_text().split('\0') if x)
  entry['processes'][label]={'pid':pid,'start_ticks':(proc/'stat').read_text().split()[21],'command':(proc/'cmdline').read_text().replace('\0',' ').strip(),'cwd':os.readlink(proc/'cwd'),'exe':os.readlink(proc/'exe'),'exe_sha256':hashlib.sha256((proc/'exe').read_bytes()).hexdigest(),'environment':env,'environment_sha256':hashlib.sha256((proc/'environ').read_bytes()).hexdigest()}
 r['fixtures'].append(entry)
packages=subprocess.run(['dpkg-query','-W','rdma-core','ibverbs-providers','libibverbs1','librdmacm1'],text=True,capture_output=True)
r['ibverbs_provider_packages']={'stdout':packages.stdout,'stderr':packages.stderr,'exit_code':packages.returncode}
r['ibv_devices']=subprocess.check_output(['ibv_devices'],text=True)
print(json.dumps(r,indent=2))
