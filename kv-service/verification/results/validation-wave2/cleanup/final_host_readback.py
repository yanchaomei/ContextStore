import pathlib,os,socket,subprocess,json,datetime
host=socket.gethostname()
spec={'skv-node2':{'pids':[2759196,2759195,2759193,2759194],'ports':[22351,22352,24351,24352,24361,24362],'root':'/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029'},'skv-node4':{'pids':[1479087,1479083],'ports':[25351,25353,25354,26379],'root':'/home/kvgroup/chaomei/contextstore_runtime_wave1_provider_20261029'},'skv-node5':{'pids':[1863559,1863490],'ports':[25551,25553,26579],'root':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029'},'skv-node6':{'pids':[],'ports':[],'root':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029'}}[host]
r={'hostname':host,'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pids_present':{str(pid):pathlib.Path('/proc',str(pid)).exists() for pid in spec['pids']},'remaining_task_executables':[]}
assert not any(r['pids_present'].values()),r
lines=subprocess.check_output(['ss','-H','-lntup'],text=True).splitlines();r['remaining_task_ports']=[x for x in lines if any(':'+str(p)+' ' in x for p in spec['ports'])];assert not r['remaining_task_ports']
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:exe=os.readlink(p/'exe');argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x]
 except (PermissionError,FileNotFoundError,ProcessLookupError):continue
 if exe.startswith(spec['root']+'/') or any(x.startswith(spec['root']+'/') and x.endswith('.py') for x in argv):r['remaining_task_executables'].append({'pid':int(p.name),'exe':exe,'argv':argv})
assert not r['remaining_task_executables'],r
r['root_preserved']=pathlib.Path(spec['root']).is_dir();assert r['root_preserved'];print(json.dumps(r,indent=2))
