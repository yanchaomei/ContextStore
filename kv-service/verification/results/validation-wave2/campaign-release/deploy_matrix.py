import pathlib,subprocess,shlex,json,hashlib,datetime,shutil
P=pathlib.Path(__file__).resolve().parent;E=P.parent/'environment/release-matrix512k';B='/home/kvgroup/chaomei/contextstore_validation_wave2_20261029';C=B+'/release-matrix512k/performance-release-control-v1'
def remote(host,cmd,data=None):
 r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','SKV_1','ssh','-o','BatchMode=yes','-o','ConnectTimeout=15',host,shlex.quote(cmd)],input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
 if r.returncode:raise RuntimeError((r.returncode,r.stderr.decode()))
 return r.stdout
ready=E/'release-matrix512k-ready-receipt.json';assert ready.exists(),'ready receipt not yet available';shutil.copy2(ready,P/'fixture-ready-receipt.json')
startup=json.loads((E/'server-startup.json').read_text());smoke=json.loads((E/'client-layout-smoke.json').read_text());assert smoke['state']=='ONE_READ_EACH_1MIB_64MIB_PASSED';assert all(r['exit_code']==0 for r in smoke['runs'])
protocol=json.loads((P/'protocol.json').read_text());protocol['at']=datetime.datetime.now(datetime.timezone.utc).isoformat();protocol['fixture_ready_sha256']=hashlib.sha256(ready.read_bytes()).hexdigest()
server=json.loads(remote('skv-node5','python3 - server',(P/'matrix_snapshot.py').read_bytes()));(P/'server-before.json').write_text(json.dumps(server,indent=2)+'\n')
for k in ['pid','start_ticks','exe_sha256']:assert server['server'][k]==startup['processes']['server'][k]
assert server['server']['exe_sha256']==protocol['server_build']['binary_sha256'];assert server['server_config']['sha256']==startup['config_sha256'];assert server['server']['environment']['CS_FORCE_DISK_READ']=='1';assert server['server']['environment']['CS_RDMA_SLAB_MB']=='512'
protocol['actual_server_before']={'pid':server['server']['pid'],'start_ticks':server['server']['start_ticks'],'binary_sha256':server['server']['exe_sha256'],'config_sha256':server['server_config']['sha256'],'environment':server['server']['environment']}
(P/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n');remote('skv-node6','mkdir '+shlex.quote(C))
for n in ['run_matrix.py','analyze_matrix.py','matrix_snapshot.py','protocol.json','server-before.json','known-pattern-hashes.json','independent-hash-replay.json','release-server-build-freeze.json','fixture-ready-receipt.json','release-profile-amendment.json']:
 remote('skv-node6','cat > '+shlex.quote(C+'/'+n),(P/n).read_bytes())
launch=f'cd {C} && tmux new-session -d -s wave2_matrix_release_v1 '+shlex.quote(f'python3 -u {C}/run_matrix.py > {C}/runner.log 2>&1')
remote('skv-node6',launch)
r={'state':'LAUNCH_DISPATCHED','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'host':'skv-node6','session':'wave2_matrix_release_v1','remote_control':C,'remote_output':B+'/release-matrix512k/performance-release-20261029-v1','control_sha256':protocol['control_sha256'],'server_profile':'release','server_sha256':server['server']['exe_sha256']}
(P/'launch-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
