import pathlib,subprocess,shlex,json,hashlib,tarfile,io,datetime
P=pathlib.Path(__file__).resolve().parent;B='/home/kvgroup/chaomei/contextstore_validation_wave2_20261029'
def remote(host,cmd,data=None):
 r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','SKV_1','ssh','-o','BatchMode=yes',host,shlex.quote(cmd)],input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
 if r.returncode:raise RuntimeError((r.returncode,r.stderr.decode()))
 return r.stdout
manifest=json.loads(remote('skv-node6','cat '+B+'/release-matrix512k/performance-release-20261029-v1/manifest.json'))
assert manifest['state']!='RUNNING','still running'
server=json.loads(remote('skv-node5','python3 - server',(P/'matrix_snapshot.py').read_bytes()));(P/'server-after.json').write_text(json.dumps(server,indent=2)+'\n')
archive=remote('skv-node6','tar -czf - -C '+B+'/release-matrix512k performance-release-20261029-v1 performance-release-control-v1')
(P/'client-evidence.tar.gz').write_bytes(archive)
with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as t:
 for m in t.getmembers():assert not m.name.startswith('/') and '..' not in pathlib.Path(m.name).parts
 t.extractall(P/'client-evidence')
before=json.loads((P/'server-before.json').read_text());offset=before['server_log']['bytes'];end=server['server_log']['bytes']
script=f'''import pathlib,sys,gzip
p=pathlib.Path({B!r})/'release-matrix512k/evidence/server.log'
with p.open('rb') as f:f.seek({offset});data=f.read({end-offset})
sys.stdout.buffer.write(gzip.compress(data))
'''
(P/'server-matrix-window.log.gz').write_bytes(remote('skv-node5','python3 -',script.encode()))
s=(P.parent/'environment/release-matrix512k/read_layout_metadata.py').read_text().replace("if 'wave2-release-matrix512k-smoke-' not in key:continue","if 'wave2-perf-release-v1-' not in key:continue").replace('assert mib in [1,64]','assert mib in [1,8,64]').replace("assert sorted(x['size_mib'] for x in objects)==[1,64]","assert sorted(x['size_mib'] for x in objects)==[1,1,1,8,8,8,64,64,64]").replace("'actual_environment':actualenv","'actual_environment':{k:v for k,v in actualenv.items() if k.startswith('CS_') or k in ['PATH','LANG','LD_LIBRARY_PATH']}")
s=s.replace("(root/'evidence/layout-metadata.json').write_text(json.dumps(r,indent=2)+'\\n');",'')
(P/'server-nine-object-metadata.json').write_bytes(remote('skv-node5','python3 -',s.encode()))
source=P.parents[2]/'work/ContextStore-runtime-wave1';protocol=json.loads((P/'protocol.json').read_text());after={n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in protocol['source_sha256']}
receipt={'state':'COLLECTED_PENDING_REVIEW','server_profile':'release','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'server_same_pid_startticks':all(server['server'][k]==before['server'][k] for k in ['pid','start_ticks','exe_sha256']),'server_same_config':server['server_config']==before['server_config'],'server_same_environment':server['server']['environment']==before['server']['environment'],'source_sha256_after':after,'source_unchanged':after==protocol['source_sha256'],'head_after':subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip(),'server_log_window_offsets':[offset,end],'client_archive_sha256':hashlib.sha256(archive).hexdigest()}
(P/'collection-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
