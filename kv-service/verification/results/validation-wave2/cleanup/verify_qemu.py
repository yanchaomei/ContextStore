import pathlib,os,subprocess,json,hashlib,datetime
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029');out=base/'wave2/cleanup-final-20261029';out.mkdir(exist_ok=False)
expected=json.loads((base/'wave2/environment/qemu_identity.json').read_text())
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'vms':{}}
for role,g in expected['qemu'].items():
 pid=g['pid'];p=pathlib.Path('/proc')/str(pid);args=[x.decode() for x in (p/'cmdline').read_bytes().split(b'\0') if x]
 ticks=(p/'stat').read_text().split()[21];exe=subprocess.check_output(['sudo','-n','readlink',str(p/'exe')],text=True).strip();digest=subprocess.check_output(['sudo','-n','sha256sum',str(p/'exe')],text=True).split()[0]
 assert ticks==g['start_ticks'] and args==g['command'] and exe==g['exe'] and digest==g['exe_sha256']
 r['vms'][role]={'pid':pid,'start_ticks':ticks,'exe':exe,'exe_sha256':digest,'command':args,'uuid':args[args.index('-uuid')+1],'disk_path':str(base/'vm-rxe'/role/'disk.qcow2'),'disk_size':(base/'vm-rxe'/role/'disk.qcow2').stat().st_size}
 r['vms'][role]['command_sha256']=hashlib.sha256((base/'wave2/environment/vm-host'/role/'command.json').read_bytes()).hexdigest()
r['base_image_sha256']=subprocess.check_output(['sha256sum',str(base/'vm-rxe/base.img')],text=True).split()[0];assert r['base_image_sha256']=='0c9811a81e6329acacbb5ae4e701a7650f85cd3f19852cd159d79ab8357b210e'
(out/'qemu-identity-guard.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
