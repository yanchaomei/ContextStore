#!/usr/bin/env python3
import ctypes,ctypes.util,datetime,hashlib,json,os,pathlib,signal,subprocess,sys,time,traceback
from analyze_matrix import validate
from matrix_snapshot import snap,sha
BASE=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029')
ROOT=BASE/'release-matrix512k/performance-release-20261029-v1'
CONTROL=BASE/'release-matrix512k/performance-release-control-v1'
EXPECTED={'cs-rail-read-bench':'6d90ed413283a513d32cb3b70428ba47cf3c7311ab47752bdc810db726bf3ca0','cs-bench':'904dd6cbf4eb096de067b9f1c2252bdce785b119ca40b57f52c7608cdf218b44'}
ENDPOINT='http://10.0.0.65:25551';RAIL='single,mlx5_1,10.0.0.65:25553,10.0.0.65:25553,1,3'
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def dump(n,d):(ROOT/n).write_text(json.dumps(d,indent=2)+'\n')
def main():
 ROOT.mkdir(exist_ok=False);start=time.monotonic();runs=[];identities={};env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C'}
 for line in (BASE/'release-matrix512k/client.env').read_text().splitlines():
  if line.startswith('export CS_'):k,v=line[7:].split('=',1);env[k]=v
 manifest={'state':'RUNNING','started':now(),'timeout_seconds_per_process':240,'maximum_runtime_seconds':5400,'environment':env,'binary_sha256':{n:sha(BASE/'bin'/n) for n in EXPECTED},'control_sha256':{p.name:sha(p) for p in CONTROL.iterdir() if p.is_file()},'output_limit_bytes':500*1024*1024,'processes_planned':120,'diagnostics_planned':6}
 manifest['server_profile']='release';manifest['server_build']=json.loads((CONTROL/'release-server-build-freeze.json').read_text());manifest['storage_interpretation']='tier_a pointer stream uses std::fs::File::open + read_exact (buffered); CS_FORCE_DISK_READ bypasses application L1; OS page cache not cleared; not physical disk or NVMe bandwidth';manifest['selection_rule']='release profile is deployment primary regardless of measured outcome; debug separate; no cross-profile single-cause attribution';dump('manifest.json',manifest);assert manifest['binary_sha256']==EXPECTED
 dump('client-before.json',snap('client'))
 known=json.loads((CONTROL/'known-pattern-hashes.json').read_text())
 dump('known-pattern-hashes.json',known)
 schedule=[]
 for s in [1,8,64]:
  for c in [1,4]:
   for pair in range(10):
    for pos,v in enumerate(['R0','R1'] if pair%2==0 else ['R1','R0']):schedule.append(dict(phase='measure',size_mib=s,concurrency=c,pair=pair,object_index=pair%3,order_position=pos,variant=v,iterations=35))
 dump('scheduled-slots.json',schedule)
 def run(name,argv,attrs,trace=False):
  assert time.monotonic()-start<5400,'runtime_budget_exhausted'
  assert sum(p.stat().st_size for p in ROOT.iterdir() if p.is_file())<500*1024*1024,'output_budget_exhausted'
  assert os.statvfs(ROOT).f_bavail*os.statvfs(ROOT).f_frsize>1024*1024*1024,'less_than_1GiB_free'
  rec=dict(name=name,argv=argv,started=now(),started_unix_ns=time.time_ns(),timeout_seconds=240,environment={**env,**({'CS_RAIL_TIMING':'1'} if trace else {})},**attrs)
  dump(name+'.started.json',rec)
  with (ROOT/(name+'.stdout')).open('wb') as out,(ROOT/(name+'.stderr')).open('wb') as err:
   p=subprocess.Popen(argv,stdout=out,stderr=err,env=rec['environment'],cwd=ROOT,start_new_session=True)
   rec['pid']=p.pid
   try:code=p.wait(timeout=240)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait();code=124
  rec.update(exit_code=code,ended=now(),ended_unix_ns=time.time_ns());runs.append(rec);dump('run-index.json',runs);dump(name+'.exit.json',rec)
  print(json.dumps({k:v for k,v in rec.items() if k not in ['argv','environment']}),flush=True)
  assert code==0,('process_failed',name,code)
  if attrs['phase']!='seed':
   v=validate(rec,(ROOT/(name+'.stdout')).read_text(),known);key=(attrs['size_mib'],attrs['object_index']);assert identities.setdefault(str(key),v['identity'])==v['identity'],'cross_process_identity_changed';dump('frozen-object-identities.json',identities);dump(name+'.validation.json',dict(state='PASS',identity=v['identity'],final_pool=v['final_pool'],sample_count=len(v['samples']),steady_median_us=v['steady_median_us']))
  dump('progress.json',{'at':now(),'completed_measurement_processes':sum(r['phase']=='measure' for r in runs),'completed_diagnostic_processes':sum(r['phase']=='diagnostic' for r in runs),'last':name,'elapsed_seconds':time.monotonic()-start})
 def command(a):
  return [str(BASE/'bin/cs-rail-read-bench'),'--coordinator',ENDPOINT,'--namespace','rust-bench','--object-key',f"wave2-perf-release-v1-{a['size_mib']}-{a['object_index']}-0/__combined__",'--rail',RAIL,'--environment','physical','--warmup','0','--iterations',str(a['iterations']),'--concurrency',str(a['concurrency']),'--verify-pattern','--connection-pool-size',str(a['concurrency'] if a['variant']=='R1' else 0)]
 try:
  for s in [1,8,64]:
   for obj in range(3):
    prefix=f'wave2-perf-release-v1-{s}-{obj}-'
    run(f'seed-{s}-{obj}',[str(BASE/'bin/cs-bench'),'--endpoint',ENDPOINT,'--layers','1','--layer-mb',str(s),'--concurrency','1','--combined','--combined-count','1','--stream','--stream-chunk-mb','2','--prefix-base',prefix,'--only-put'],{'phase':'seed','size_mib':s,'object_index':obj})
  for a in schedule:run(f"{a['size_mib']}MiB-c{a['concurrency']}-pair{a['pair']:02d}-{a['variant']}",command(a),a)
  for s in [1,8,64]:
   for v in ['R0','R1']:
    a=dict(phase='diagnostic',size_mib=s,concurrency=1,object_index=0,variant=v,iterations=6)
    run(f'diagnostic-{s}MiB-{v}',command(a),a,trace=True)
  manifest['state']='COMPLETED'
 except Exception as e:
  manifest['state']='STOPPED_FAILURE_OR_INCOMPLETE';manifest['error']=repr(e);dump('failure.json',{'at':now(),'error':repr(e),'traceback':traceback.format_exc()});traceback.print_exc()
 finally:
  manifest['ended']=now();manifest['binary_sha256_after']={n:sha(BASE/'bin'/n) for n in EXPECTED};manifest['binaries_unchanged']=manifest['binary_sha256']==manifest['binary_sha256_after'];manifest['run_count']=len(runs);dump('manifest.json',manifest);dump('client-after.json',snap('client'))
  with (ROOT/'replay-commands.jsonl').open('w') as f:
   for r in runs:f.write(json.dumps({'name':r['name'],'argv':r['argv'],'environment':r['environment']})+'\n')
  dump('raw-sha256.json',{p.name:sha(p) for p in sorted(ROOT.iterdir()) if p.is_file() and p.name!='raw-sha256.json'})
 print(json.dumps(manifest),flush=True)
if __name__=='__main__':main()
