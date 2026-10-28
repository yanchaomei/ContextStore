import pathlib,json,hashlib,os,subprocess,datetime
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029');root=base/'wave2/environment'
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'qemu':{}}
for role in ['server','client']:
 p=root/'vm-host'/role;pid=int(subprocess.check_output(['sudo','-n','cat',str(p/'qemu.pid')],text=True));proc=pathlib.Path('/proc')/str(pid)
 command=json.loads((p/'command.json').read_text())
 actual=(proc/'cmdline').read_bytes().split(b'\0');actual=[s.decode() for s in actual if s]
 assert actual==command[2:],(role,actual,command)
 r['qemu'][role]={'pid':pid,'start_ticks':(proc/'stat').read_text().split()[21],'exe':subprocess.check_output(['sudo','-n','readlink',str(proc/'exe')],text=True).strip(),'exe_sha256':subprocess.check_output(['sudo','-n','sha256sum',str(proc/'exe')],text=True).split()[0],'cwd':subprocess.check_output(['sudo','-n','readlink',str(proc/'cwd')],text=True).strip(),'command':actual,'command_sha256':hashlib.sha256((p/'command.json').read_bytes()).hexdigest()}
 out=root/f'{role}-guest-environment.tar.gz'
 with out.open('wb') as dest:
  subprocess.run(['bash',str(base/'vm-rxe/guest_ssh.sh'),role,'tar -czf - -C /home/railtest/cswave1/wave2 evidence '+('server.toml' if role=='server' else 'client.env')],check=True,stdout=dest)
 r[role+'_guest_archive_sha256']=hashlib.sha256(out.read_bytes()).hexdigest()
(root/'qemu_identity.json').write_text(json.dumps(r,indent=2)+'\n')
print(json.dumps(r,indent=2))
