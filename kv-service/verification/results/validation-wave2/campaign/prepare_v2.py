import pathlib,subprocess,shlex,json,hashlib,datetime,shutil
P=pathlib.Path(__file__).resolve().parent;B='/home/kvgroup/chaomei/contextstore_validation_wave2_20261029';C=B+'/matrix512k/hash-compatibility-v1'
def remote(host,cmd,data=None):
 r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','SKV_1','ssh','-o','BatchMode=yes',host,shlex.quote(cmd)],input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
 if r.returncode:raise RuntimeError((r.returncode,r.stderr.decode()))
 return r.stdout
A=P/'attempt1-controls';A.mkdir()
for n in ['run_matrix.py','analyze_matrix.py','matrix_snapshot.py','deploy_matrix.py','protocol.json','server-before.json','launch-receipt.json']:shutil.copy2(P/n,A/n)
script=f'''import pathlib,subprocess,json,datetime
p=pathlib.Path({C!r});p.mkdir()
a=[{B!r}+'/bin/cs-rail-read-bench','--coordinator','http://10.0.0.65:25551','--namespace','rust-bench','--object-key','wave2-perf-v1-8-0-0/__combined__','--rail','single,mlx5_1,10.0.0.65:25553,10.0.0.65:25553,1,3','--environment','physical','--warmup','0','--iterations','1','--concurrency','1','--verify-pattern','--connection-pool-size','0']
r=subprocess.run(a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={{'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C'}},timeout=240)
(p/'8MiB.stdout').write_bytes(r.stdout);(p/'8MiB.stderr').write_bytes(r.stderr)
d={{'argv':a,'exit_code':r.returncode,'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'stdout':r.stdout.decode(),'stderr':r.stderr.decode()}};(p/'receipt.json').write_text(json.dumps(d,indent=2));print(json.dumps(d))
'''
r=json.loads(remote('skv-node6','python3 -',script.encode()));(P/'hash-compatibility-probe.json').write_text(json.dumps(r,indent=2)+'\n');assert r['exit_code']==0
from analyze_matrix import records
expected,=records(r['stdout'],'expected_pattern');sample,=records(r['stdout'],'sample');assert expected['xxh3']==sample['xxh3'] and int(sample['bytes'])==8*1048576
known={'1':'c061e1dcd62f6c77','8':expected['xxh3'],'64':'a0a4cbfa5cad46af'};(P/'known-pattern-hashes.json').write_text(json.dumps(known,indent=2)+'\n')
note={'state':'HARNESS_FAILURE_SUPERSEDED_PRESERVED','failed_campaign':B+'/matrix512k/performance-20261029-v1','failed_slot':'1MiB-c1-pair00-R0','benchmark_exit':0,'read_samples':35,'reason':'External system libxxhash 0.7.3 expected hash differed from frozen twox-hash 1.6.3 implementation. CLI computes full independent index_mod_251 expected buffer and validates every sample against it; sample and CLI expectation agree. No runtime defect observed.','system_libxxhash_0_7_3_hashes':{'1':'f200297ce44ea4d7','8':'3e4078e898678079','64':'e610245a1a19a90f'},'frozen_benchmark_expected_hashes':known,'correction':'v2 uses frozen benchmark known-pattern hashes, preserving CLI full expected-pattern validation; old failed primary slot excluded by explicit full campaign supersession, no mixed blocks. Nine existing object seeds reused without overwrite.'}
(P/'harness-failure-supersession.json').write_text(json.dumps(note,indent=2)+'\n')
s=(P/'run_matrix.py').read_text().replace("performance-20261029-v1'","performance-20261029-v2'").replace("performance-control-v1'","performance-control-v2'")
a=s.index(' lib=ctypes.CDLL');z=s.index(" dump('known-pattern-hashes.json',known)",a)
s=s[:a]+" known=json.loads((CONTROL/'known-pattern-hashes.json').read_text())\n"+s[z:]
a=s.index('  for s in [1,8,64]:\n   for obj in range(3):');z=s.index('  for a in schedule:',a)
s=s[:a]+'''  old=BASE/'matrix512k/performance-20261029-v1'
  for rec in json.loads((old/'run-index.json').read_text()):
   if rec['phase']!='seed':continue
   assert rec['exit_code']==0
   for suffix in ['.stdout','.stderr','.started.json','.exit.json']:(ROOT/(rec['name']+suffix)).write_bytes((old/(rec['name']+suffix)).read_bytes())
   runs.append({**rec,'inherited_from_campaign':str(old)})
  dump('run-index.json',runs)
'''+s[z:]
(P/'run_matrix.py').write_text(s)
s=(P/'deploy_matrix.py').read_text().replace('performance-control-v1','performance-control-v2').replace('wave2_matrix_perf_v1','wave2_matrix_perf_v2').replace('performance-20261029-v1','performance-20261029-v2').replace("['run_matrix.py','analyze_matrix.py','matrix_snapshot.py','protocol.json','server-before.json']","['run_matrix.py','analyze_matrix.py','matrix_snapshot.py','protocol.json','server-before.json','known-pattern-hashes.json','harness-failure-supersession.json']")
(P/'deploy_matrix.py').write_text(s)
print(json.dumps(note,indent=2))
