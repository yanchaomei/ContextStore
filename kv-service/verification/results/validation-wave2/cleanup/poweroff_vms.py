import pathlib,json,subprocess,hashlib,datetime,os,time,tarfile,io
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029');out=base/'wave2/cleanup-final-20261029';guard=json.loads((out/'qemu-identity-guard.json').read_text());wrapper=base/'vm-rxe/guest_ssh.sh'
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def check(role):
 g=guard['vms'][role];p=pathlib.Path('/proc')/str(g['pid']);assert (p/'stat').read_text().split()[21]==g['start_ticks'];assert [x.decode() for x in (p/'cmdline').read_bytes().split(b'\0') if x]==g['command']
 assert subprocess.check_output(['sudo','-n','sha256sum',str(p/'exe')],text=True).split()[0]==g['exe_sha256']
def host_archive(name):
 p=out/(name+'.tar.gz');files={}
 with tarfile.open(p,'w:gz') as t:
  for src in sorted((base/'wave2/environment/vm-host').rglob('*')):
   if not src.is_file():continue
   try:data=src.read_bytes()
   except PermissionError:data=subprocess.check_output(['sudo','-n','cat',str(src)])
   key=str(src.relative_to(base));item=tarfile.TarInfo(key);item.size=len(data);t.addfile(item,io.BytesIO(data));files[key]={'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
 return {'path':str(p),'sha256':sha(p),'files':files}
probe="""import pathlib,os,json,socket
r={'hostname':socket.gethostname(),'active_task_processes':[]}
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try: exe=os.readlink(p/'exe');argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\\0') if x]
 except (FileNotFoundError,PermissionError,ProcessLookupError):continue
 if exe.startswith('/home/railtest/') or any(x.startswith('/home/railtest/') and x.endswith('.py') for x in argv):r['active_task_processes'].append({'pid':int(p.name),'exe':exe,'argv':argv})
assert not r['active_task_processes'],r
print(json.dumps(r))
"""
r={'started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'vms':{},'host_pre_archive':host_archive('host-pre-poweroff-logs')}
for role in ['server','client']:
 check(role)
 p=subprocess.run(['bash',str(wrapper),role,'python3 -'],input=probe,text=True,capture_output=True);assert p.returncode==0,(role,p.stderr);state=json.loads(p.stdout);assert state['hostname']=='csw1-rxe-'+role
 (out/(role+'-guest-pre-poweroff.json')).write_text(json.dumps(state,indent=2)+'\n')
 check(role)
 cmd=subprocess.run(['bash',str(wrapper),role,'sudo -n poweroff'],text=True,capture_output=True,timeout=15)
 (out/(role+'-poweroff.stdout')).write_text(cmd.stdout);(out/(role+'-poweroff.stderr')).write_text(cmd.stderr)
 pid=guard['vms'][role]['pid'];proc=pathlib.Path('/proc')/str(pid);deadline=time.monotonic()+45
 while proc.exists() and time.monotonic()<deadline:time.sleep(.2)
 assert not proc.exists(),(role,pid,'QEMU still present; no host kill attempted')
 r['vms'][role]={'pid':pid,'start_ticks':guard['vms'][role]['start_ticks'],'uuid':guard['vms'][role]['uuid'],'guest_hostname':state['hostname'],'active_task_processes_before':state['active_task_processes'],'poweroff_ssh_exit':cmd.returncode,'qemu_exited':True}
 (out/'poweroff-progress.json').write_text(json.dumps(r,indent=2)+'\n');print(role,'powered off',flush=True)
r['host_post_archive']=host_archive('host-post-poweroff-logs')
r['disk_and_firmware_hashes']={}
for role in ['server','client']:
 for name in ['disk.qcow2','OVMF_VARS.fd','seed.iso','command.json']:
  p=base/'vm-rxe'/role/name;assert p.exists();r['disk_and_firmware_hashes'][str(p)]={'size':p.stat().st_size,'sha256':sha(p)}
r['base_image_sha256_after']=sha(base/'vm-rxe/base.img');assert r['base_image_sha256_after']==guard['base_image_sha256']
listen=subprocess.check_output(['ss','-H','-lntup'],text=True).splitlines();r['remaining_task_ports']=[x for x in listen if any(':'+str(port)+' ' in x for port in [22351,22352,24351,24352,24361,24362])];assert not r['remaining_task_ports']
r['preserved']={str(p):p.exists() for p in [base/'vm-rxe',base/'vm-rxe/railtest_ed25519',base/'tools',base/'src-baseline',base/'src-candidate',base/'wave2']};assert all(r['preserved'].values())
r['finished']=datetime.datetime.now(datetime.timezone.utc).isoformat();(out/'poweroff-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print('VM_SHUTDOWN_COMPLETE')
