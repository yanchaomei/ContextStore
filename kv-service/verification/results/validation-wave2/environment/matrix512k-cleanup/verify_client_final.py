import pathlib,json,hashlib,os,datetime
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029');out=base/'matrix512k-cleanup-20261029';prior=json.loads((out/'client-final-receipt.json').read_text())
checked=0
for name,entry in prior['files'].items():
 p=base/name;assert p.is_file() and p.stat().st_size==entry['size'];assert hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256'],name;checked+=1
active=[]
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  argv=[s.decode(errors='replace') for s in (p/'cmdline').read_bytes().split(b'\0') if s];exe=os.readlink(p/'exe')
 except (FileNotFoundError,PermissionError,ProcessLookupError):continue
 if exe.startswith(str(base/'bin')) or any(str(base/'matrix512k/performance-control') in x and x.endswith('.py') for x in argv):active.append({'pid':int(p.name),'exe':exe,'argv':argv})
assert not active,active
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'checked_file_count':checked,'all_hashes_unchanged':True,'tree_manifest_sha256':prior['tree_manifest_sha256'],'active_task_measurement_processes':active,'client_processes_stopped_by_cleanup':0}
(out/'client-final-readback.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
