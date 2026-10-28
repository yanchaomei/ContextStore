import pathlib,json,os,datetime
out={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'host':os.uname().nodename,'processes':[]}
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdecimal():continue
 try:
  cmd=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode()
  if '/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/bin/cs-rail-read-bench ' not in cmd and not cmd.startswith('python3 -u /home/kvgroup/chaomei/contextstore_validation_wave2_20261029/matrix512k/performance-control-v2/run_matrix.py'):continue
  if 'sshd' in cmd or cmd.startswith('sh ') or cmd.startswith('bash '):continue
  out['processes'].append({'pid':int(p.name),'command':cmd,'limits':(p/'limits').read_text(),'status':(p/'status').read_text()})
 except (PermissionError,FileNotFoundError,ProcessLookupError):pass
print(json.dumps(out,indent=2))
