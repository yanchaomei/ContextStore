import pathlib,subprocess,shlex,json,hashlib,datetime
P=pathlib.Path(__file__).resolve().parent;B='/home/kvgroup/chaomei/contextstore_validation_wave2_20261029';C=B+'/matrix512k/performance-control-v2'
def remote(host,cmd,data=None):
 v=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','SKV_1','ssh','-o','BatchMode=yes','-o','ConnectTimeout=15',host,shlex.quote(cmd)]
 r=subprocess.run(v,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
 if r.returncode:raise RuntimeError((v,r.returncode,r.stderr.decode()))
 return r.stdout
freeze=json.loads((P.parent/'campaign-freeze.json').read_text())
protocol={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'matrix':freeze['R_matrix'],'per_process_timeout_s':240,'campaign_max_runtime_s':5400,'output_limit_bytes':500*1024*1024,'minimum_free_disk_bytes':1024**3,'schedule':'sizes1,8,64; each C1 thenC4; pairs0..9; alternated R0/R1','failure_policy':'stop and preserve original slot; no same-slot retry','code_sha256':{n:hashlib.sha256((P/n).read_bytes()).hexdigest() for n in ['run_matrix.py','analyze_matrix.py','matrix_snapshot.py']},'source_runtime_head':subprocess.check_output(['git','-C',str(P.parents[2]/'work/ContextStore-runtime-wave1'),'rev-parse','HEAD'],text=True).strip()}
protocol['source_sha256']={n:hashlib.sha256((P.parents[2]/'work/ContextStore-runtime-wave1'/n).read_bytes()).hexdigest() for n in ['kv-service/client-rs/src/bin/rail_read_bench.rs','kv-service/client-rs/src/rail_read.rs','kv-service/client-rs/src/rail_read/connection_pool.rs','kv-service/client-rs/src/rdma.rs','kv-service/client-rs/src/lib.rs','Cargo.lock','Cargo.toml']}
(P/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
server=json.loads(remote('skv-node5','python3 - server',(P/'matrix_snapshot.py').read_bytes()))
(P/'server-before.json').write_text(json.dumps(server,indent=2)+'\n')
assert server['server']['pid']==1882561 and server['server']['start_ticks']=='313266078'
assert server['server']['exe_sha256']=='0e9c081f15f809839684bab64e24b75c547ec1ce4e0e1a4a2060f6146c76c87c'
assert server['server_config']['sha256']=='3e6187f88ee08c5664bd838e4034b2dc100ee43a129eefb3261e4df133c77380'
assert server['server']['environment']['CS_FORCE_DISK_READ']=='1' and server['server']['environment']['CS_RDMA_SLAB_MB']=='512'
remote('skv-node6','mkdir '+shlex.quote(C))
for n in ['run_matrix.py','analyze_matrix.py','matrix_snapshot.py','protocol.json','server-before.json','known-pattern-hashes.json','harness-failure-supersession.json']:
 remote('skv-node6','cat > '+shlex.quote(C+'/'+n),(P/n).read_bytes())
launch=f'cd {C} && tmux new-session -d -s wave2_matrix_perf_v2 '+shlex.quote(f'python3 -u {C}/run_matrix.py > {C}/runner.log 2>&1')
remote('skv-node6',launch)
receipt={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'state':'LAUNCH_DISPATCHED','host':'skv-node6','session':'wave2_matrix_perf_v2','remote_control':C,'remote_output':B+'/matrix512k/performance-20261029-v2','code_sha256':protocol['code_sha256']}
(P/'launch-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
