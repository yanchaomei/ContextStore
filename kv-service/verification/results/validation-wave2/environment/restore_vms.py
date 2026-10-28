import pathlib,json,hashlib,socket,subprocess,datetime,time,os
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029')
vmroot=base/'vm-rxe'; root=base/'wave2/environment/vm-host'
assert not root.exists(), 'Refuse overwrite wave2 VM logs'
cleanup=json.loads((base/'cleanup-20261029/shutdown_receipt.json').read_text())
expected={'server':{'uuid':'3cf534ce-653d-4171-9574-bba582db6338','disk.qcow2':'2b3b838ec6e897d0fca60eaf5e51e818dd84c356276135ed6d8eb66686bce50c','OVMF_VARS.fd':'1136974340633f6664c4f620ebfe864755c46f380742f532bd416e1193d6b3a2','command.json':'eb99672d9d9f240ef9577ecf51ff884bd08ed152f2499c1b2b686ebce70b9b89'},'client':{'uuid':'782360c4-1d34-4a3e-9746-d9f96612d2dc','disk.qcow2':'0aef372353195af1d855ba7fd8a4922dac1f6cbc718f14ddc58cc85d2a94148e','OVMF_VARS.fd':'7f4f46240a26a62a207007558b6453c9f7454c322df9f75a2c922cc5e393b36e','command.json':'5319de56c1f2a81fa170568e4dc1912e74b5fa9d167a6b5df3164044b9afad3b'}}
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
checked={}
for role,values in expected.items():
 for name,digest in values.items():
  if name=='uuid':continue
  p=vmroot/role/name;actual=sha(p);assert actual==digest,(str(p),actual,digest);checked[str(p)]=actual
basehash=sha(vmroot/'base.img');assert basehash=='0c9811a81e6329acacbb5ae4e701a7650f85cd3f19852cd159d79ab8357b210e'
sockets=[]
for kind,ports in [(socket.SOCK_STREAM,[22351,22352]),(socket.SOCK_DGRAM,[24351,24352,24361,24362])]:
 for port in ports:
  s=socket.socket(socket.AF_INET,kind);s.bind(('127.0.0.1',port));sockets.append(s)
root.mkdir(parents=True)
receipt={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'verified_paths':checked,'base_sha256':basehash,'all_chosen_ports_free':True,'vms':{}}
(root/'preflight.json').write_text(json.dumps(receipt,indent=2)+'\n')
for s in sockets:s.close()
for role in expected:
 path=root/role;path.mkdir()
 command=json.loads((vmroot/role/'command.json').read_text())
 assert command[command.index('-uuid')+1]==expected[role]['uuid']
 assert sum('socket,id=rail' in x and 'udp=' in x for x in command)==2
 command[command.index('-serial')+1]='file:'+str(path/'serial.log')
 command[command.index('-pidfile')+1]=str(path/'qemu.pid')
 (path/'command.json').write_text(json.dumps(command,indent=2)+'\n')
 p=subprocess.Popen(command,cwd=base,stdout=open(path/'qemu.log','wb'),stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
 receipt['vms'][role]={'launcher_pid':p.pid,'uuid':expected[role]['uuid'],'command':command}
(root/'launch_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2))
