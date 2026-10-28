import pathlib,json,subprocess,hashlib,tarfile,sys,shlex
root=pathlib.Path(__file__).resolve().parent;target=sys.argv[1];phase=sys.argv[2];r=json.loads((root/f'{target}-{phase}.json').read_text());c=r['collection'];path=c['archive_path'];dest=root/f'{target}-{phase}-logs-config-snapshots.tar.gz'
if target.startswith('skv'):cmd=['ssh','-oBatchMode=yes','SKV_'+target[-1],'cat '+shlex.quote(path)]
else:cmd=['ssh','-oBatchMode=yes','SKV_2','bash /home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/vm-rxe/guest_ssh.sh '+target[6:]+' cat '+shlex.quote(path)]
with dest.open('wb') as out,(root/f'{target}-{phase}-fetch.stderr').open('wb') as err:subprocess.run(cmd,stdout=out,stderr=err,check=True)
h=hashlib.sha256(dest.read_bytes()).hexdigest();assert h==c['archive_sha256'];count=0
with tarfile.open(dest) as t:
 for p in t:
  if not p.isfile():continue
  data=t.extractfile(p).read();assert hashlib.sha256(data).hexdigest()==c['archived_files'][p.name]['sha256'],p.name;count+=1
assert count==len(c['archived_files'])
(root/f'{target}-{phase}-local-verification.json').write_text(json.dumps({'archive_sha256':h,'files_verified':count,'bytes':dest.stat().st_size},indent=2)+'\n');print(target,phase,count,'files',dest.stat().st_size,'bytes')
