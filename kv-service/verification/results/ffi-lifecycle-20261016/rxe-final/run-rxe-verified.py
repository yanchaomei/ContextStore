from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess,json,os,time,hashlib
root=Path('/home/railtest/ffi-20261016'); out=root/'results-verified'; out.mkdir(exist_ok=False)
def run(rail):
 server=f'10.{31+rail}.0.2:{55153+rail}'; device=f'rxe_c{rail}'; records=[]
 for baseline,size,mode in [(True,1,'bad_tag')]+[(False,s,m) for s in (1,8) for m in ('bad_tag','timeout')]:
  name=f'rail{rail}-'+('baseline' if baseline else 'candidate')+f'-{mode}-{size}m'
  command=['python3','ffi_rxe_late_write.py','--library','baseline.so' if baseline else 'candidate.so','--module','rdma_client.py','--server',server,'--device',device,'--key',f'3:ffi-20261016-{time.time_ns()}','--bytes',str(size*1048576),'--failure-mode',mode,'--output',str(out/(name+'.json'))]
  if baseline: command+=['--unsafe-baseline']
  (out/(name+'.command.json')).write_text(json.dumps(command)+'\n')
  with (out/(name+'.log')).open('wb') as log: p=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT,timeout=90)
  (out/(name+'.exit')).write_text(str(p.returncode)+'\n')
  receipt=json.loads((out/(name+'.json')).read_text()) if (out/(name+'.json')).exists() else {}
  passed=(p.returncode==1 and receipt.get('exact_payload_corruption') is True and receipt.get('reused_sentinel_intact') is False and receipt.get('sentinel_sha256')==receipt.get('expected_payload_sha256')) if baseline else (p.returncode==0 and receipt.get('reused_sentinel_intact') is True and receipt.get('same_handle_reconnect_put_get_full_content') is True)
  records.append(dict(case=name,passed=passed,exit=p.returncode));print(name,p.returncode,passed,flush=True)
 name=f'rail{rail}-put-cq';env=os.environ.copy();env.update(LD_PRELOAD=str(root/'verbs_cq_delay.so'),CS_CQ_DELAY_LOG=str(out/(name+'.events')))
 command=['python3','ffi_rxe_put_reconnect.py','--library','candidate.so','--module','rdma_client.py','--server',server,'--device',device,'--output',str(out/(name+'.json'))]
 (out/(name+'.command.json')).write_text(json.dumps(command)+'\n')
 with (out/(name+'.log')).open('wb') as log:p=subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=90)
 (out/(name+'.exit')).write_text(str(p.returncode)+'\n'); records.append(dict(case=name,passed=p.returncode==0,exit=p.returncode));print(name,p.returncode,p.returncode==0,flush=True)
 return records
with ThreadPoolExecutor(max_workers=2) as pool: results=sum(list(pool.map(run,[0,1])),[])
(out/'summary.json').write_text(json.dumps({'cases':results,'all_passed':all(r['passed'] for r in results)},indent=2)+'\n')
raise SystemExit(0 if all(r['passed'] for r in results) else 1)
