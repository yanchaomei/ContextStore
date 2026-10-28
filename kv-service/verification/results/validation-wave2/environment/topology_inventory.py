import pathlib,socket,subprocess,json,datetime,os
r={'hostname':socket.gethostname(),'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'kernel':subprocess.check_output(['uname','-a'],text=True),'rdma_links':subprocess.check_output(['rdma','link','show'],text=True),'ip_addresses':subprocess.check_output(['ip','-br','addr'],text=True),'devices':[]}
for p in pathlib.Path('/sys/class/infiniband').glob('*'):
 device={'name':p.name,'device_path':str((p/'device').resolve()),'numa_node':(p/'device/numa_node').read_text().strip() if (p/'device/numa_node').exists() else None,'ports':[]}
 for q in (p/'ports').glob('*'):
  port={'id':q.name,'state':(q/'state').read_text().strip(),'phys_state':(q/'phys_state').read_text().strip(),'gids':{}}
  for gid in (q/'gids').glob('*'):
   value=gid.read_text().strip()
   if value=='0000:0000:0000:0000:0000:0000:0000:0000':continue
   port['gids'][gid.name]={'value':value,'type':(q/'gid_attrs/types'/gid.name).read_text().strip() if (q/'gid_attrs/types'/gid.name).exists() else None,'netdev':(q/'gid_attrs/ndevs'/gid.name).read_text().strip() if (q/'gid_attrs/ndevs'/gid.name).exists() else None}
  device['ports'].append(port)
 r['devices'].append(device)
print(json.dumps(r,indent=2))
