import pathlib,os,json,hashlib,datetime,tarfile,socket
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029');out=base/'matrix512k-cleanup-20261029';out.mkdir()
active=[]
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  argv=[s.decode(errors='replace') for s in (p/'cmdline').read_bytes().split(b'\0') if s];exe=os.readlink(p/'exe')
 except (FileNotFoundError,PermissionError,ProcessLookupError):continue
 if exe.startswith(str(base/'bin')) or any(str(base/'matrix512k/performance-control') in x and x.endswith('.py') for x in argv):
  active.append({'pid':int(p.name),'exe':exe,'argv':argv,'start_ticks':(p/'stat').read_text().split()[21]})
assert not active,active
files={}
for parent in [base/'matrix512k',base/'bin',base/'evidence']:
 for p in sorted(parent.rglob('*')):
  if p.is_file():files[str(p.relative_to(base))]={'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
p=base/'client.env';files[str(p.relative_to(base))]={'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
with tarfile.open(out/'client-final-results.tar.gz','w:gz') as t:
 for name in ['matrix512k','client.env','evidence']:t.add(base/name,arcname=name)
archive=out/'client-final-results.tar.gz'
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hostname':socket.gethostname(),'active_task_measurement_processes':active,'files':files,'file_count':len(files),'tree_manifest_sha256':hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'results_archive':str(archive),'results_archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'results_archive_size':archive.stat().st_size,'action':'read-only process and result verification; no client process stopped; binaries and all directories preserved'}
(out/'client-final-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
