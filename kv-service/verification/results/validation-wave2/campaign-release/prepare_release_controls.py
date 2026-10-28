import pathlib,shutil,json,hashlib
P=pathlib.Path(__file__).resolve().parent;OLD=P.parent/'campaign';ENV=P.parent/'environment/release-matrix512k';SRC=P.parents[2]/'work/ContextStore-runtime-wave1'
profile=json.loads((ENV/'server-build-source-evidence/release-freeze.json').read_text());amendment=json.loads((P.parent/'release-profile-amendment.json').read_text());original=json.loads((P.parent/'campaign-freeze.json').read_text())
for n in ['known-pattern-hashes.json','independent-hash-replay.json']:shutil.copy2(OLD/n,P/n)
shutil.copytree(OLD/'hash-oracle',P/'hash-oracle',dirs_exist_ok=True)
shutil.copy2(ENV/'server-build-source-evidence/release-freeze.json',P/'release-server-build-freeze.json')
shutil.copy2(ENV/'server-build-source-evidence/green-source-sha256.json',P/'server-build-source-sha256.json')
shutil.copy2(P.parent/'release-profile-amendment.json',P/'release-profile-amendment.json')
s=(OLD/'run_matrix.py').read_text().replace('matrix512k/performance-20261029-v2','release-matrix512k/performance-release-20261029-v1').replace('matrix512k/performance-control-v2','release-matrix512k/performance-release-control-v1').replace('matrix512k/client.env','release-matrix512k/client.env').replace('wave2-perf-v1-','wave2-perf-release-v1-')
a=s.index("  old=BASE/'matrix512k/performance-20261029-v1'");z=s.index('  for a in schedule:',a)
s=s[:a]+'''  for s in [1,8,64]:
   for obj in range(3):
    prefix=f'wave2-perf-release-v1-{s}-{obj}-'
    run(f'seed-{s}-{obj}',[str(BASE/'bin/cs-bench'),'--endpoint',ENDPOINT,'--layers','1','--layer-mb',str(s),'--concurrency','1','--combined','--combined-count','1','--stream','--stream-chunk-mb','2','--prefix-base',prefix,'--only-put'],{'phase':'seed','size_mib':s,'object_index':obj})
'''+s[z:]
s=s.replace("dump('manifest.json',manifest);assert", "manifest['server_profile']='release';manifest['server_build']=json.loads((CONTROL/'release-server-build-freeze.json').read_text());manifest['storage_interpretation']='tier_a pointer stream uses std::fs::File::open + read_exact (buffered); CS_FORCE_DISK_READ bypasses application L1; OS page cache not cleared; not physical disk or NVMe bandwidth';manifest['selection_rule']='release profile is deployment primary regardless of measured outcome; debug separate; no cross-profile single-cause attribution';dump('manifest.json',manifest);assert")
(P/'run_matrix.py').write_text(s)
s=(OLD/'matrix_snapshot.py').read_text().replace("B/'matrix512k/", "B/'release-matrix512k/")
s=s.replace("['cs-bench','cs-rail-read-bench'] if role=='client' else ['contextstore-server']", "['cs-bench','cs-rail-read-bench'] if role=='client' else []")
s=s.replace("pid=1882561;p=pathlib.Path('/proc')/str(pid)","pid=int((B/'release-matrix512k/evidence/server.pid').read_text());p=pathlib.Path('/proc')/str(pid)")
s=s.replace("  log=B/", "  d['binaries']['contextstore-server']=sha(p/'exe')\n  d['server']['limits']=(p/'limits').read_text()\n  log=B/")
s=s.replace("\n return d\n", "\n d['self_limits']=pathlib.Path('/proc/self/limits').read_text()\n return d\n")
(P/'matrix_snapshot.py').write_text(s)
s=(OLD/'analyze_matrix.py').read_text().replace('forced server disk read, warm OS page cache','forced application storage path, buffered tier_a file reads, warm OS page cache');s=s.replace("(root/'analysis.json').write_text", "result['server_profile']='release';result['deployment_selection']='main deployment result regardless of direction or magnitude; debug separately retained';result['storage_interpretation']='tier_a4 workers; pointer read via std::fs::File::open and read_exact, buffered; forced path bypasses application L1 but does not prove physical media access; OS page cache not cleared';result['cross_profile_boundary']='debug/release have different build features and source snapshot; no sole-cause compiler or guard speedup attribution'\n (root/'analysis.json').write_text")
(P/'analyze_matrix.py').write_text(s)
for n in ['analyze_diagnostics.py','ppt_tables.py']:
 s=(OLD/n).read_text().replace('performance-20261029-v2','performance-release-20261029-v1').replace('forced disk read, warm OS page cache','forced application storage path, buffered tier_a file reads, warm OS page cache')
 (P/n).write_text(s)
protocol={'R_matrix_original':original['R_matrix'],'amendment':amendment,'server_build':profile,'client_binary_sha256':original['R_matrix']['benchmark_sha256'],'hash_oracle_replay_sha256':json.loads((P/'independent-hash-replay.json').read_text())['replay_script_sha256'],'timeout_seconds_per_process':240,'maximum_runtime_seconds':5400,'output_limit_bytes':500*1024*1024,'minimum_free_bytes':1024**3,'schedule':'sizes1,8,64; C1 thenC4; pair0..9; R0/R1 alternating order; three fresh keys per size, pair modulo3','source_runtime_head':'1c3ee135d19fece67507b51acd1fc51ae9541208','source_sha256':{},'control_sha256':{n:hashlib.sha256((P/n).read_bytes()).hexdigest() for n in ['run_matrix.py','analyze_matrix.py','matrix_snapshot.py']}}
for n in ['kv-service/client-rs/src/bin/rail_read_bench.rs','kv-service/client-rs/src/rail_read.rs','kv-service/client-rs/src/rail_read/connection_pool.rs','kv-service/client-rs/src/rdma.rs','kv-service/client-rs/src/lib.rs','Cargo.lock','Cargo.toml','Makefile','kv-service/Makefile','kv-service/server/Cargo.toml','kv-service/server/src/rdma/server.rs','kv-service/server/src/io_executor/tier_a.rs']:protocol['source_sha256'][n]=hashlib.sha256((SRC/n).read_bytes()).hexdigest()
(P/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
print(json.dumps({'state':'CONTROLS_FROZEN_WAITING_READY_FIXTURE','server_profile':profile['profile'],'server_sha256':profile['binary_sha256'],'control_sha256':protocol['control_sha256']},indent=2))
