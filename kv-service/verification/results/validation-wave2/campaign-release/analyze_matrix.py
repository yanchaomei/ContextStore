#!/usr/bin/env python3
import csv, hashlib, json, math, pathlib, random, statistics, sys

def records(t,k):return [dict(x.split('=',1) for x in l.split(',')[1:] if '=' in x) for l in t.splitlines() if l.startswith(k+',')]
def gm(v):return math.exp(statistics.mean(map(math.log,v)))
def validate(run,text,known):
 c,s,n=run['concurrency'],run['size_mib'],run['iterations']; samples=records(text,'sample')
 assert len(samples)==n*c,('sample_count',len(samples),n*c)
 before,=records(text,'object_identity');after,=records(text,'object_identity_after');expected,=records(text,'expected_pattern')
 assert before==after,'identity_changed'
 assert int(before['stripe_count'])==s*2 and int(before['chunk_size'])==524288,'layout'
 assert expected=={'pattern':'index_mod_251','bytes':str(s*1048576),'xxh3':known[str(s)]},('expected_pattern',expected)
 pairs=[]
 for x in samples:
  batch=int(x['iteration' if c==1 else 'batch']);worker=int(x.get('worker',0))
  assert x['environment']=='physical' and int(x['rails'])==1 and int(x['bytes'])==s*1048576 and x['xxh3']==known[str(s)],'bytes/hash/path'
  assert int(x['latency_us'])>0,'latency'
  pairs.append((batch,worker))
 assert sorted(pairs)==[(i,w) for i in range(1,n+1) for w in range(c)],'batch/worker_coverage'
 modes=records(text,'mode'); mode=[m for m in modes if 'connection_pool_size' in m][0]
 assert mode==dict(connection_pool_size=str(c if run['variant']=='R1' else 0),recovery_timeout_ms='None',verify_pattern='true',warmup='0',iterations=str(n),concurrency=str(c)),('mode',mode)
 assert {'direct_staging':'false'} in modes and {'contiguous_schedule':'false'} in modes,'staging'
 pools=records(text,'pool_stats');assert [x['phase'] for x in pools]==['before_warmup','after_warmup','final'],'pool phases'
 final=pools[-1]
 if run['variant']=='R1':
  for p in pools[:2]:assert p['enabled']=='true' and all(int(v)==0 for k,v in p.items() if k not in ('enabled','phase')),'nonzero cold pool'
  assert final['enabled']=='true' and 1<=int(final['created'])<=c,'pool created bound'
  assert int(final['created'])+int(final['reused'])==n*c,'pool accounting'
  assert all(int(final[k])==0 for k in ['retired','rejected','connecting','active','retiring']),'pool active/retirement'
  assert int(final['idle'])==int(final['live'])==int(final['created']),'pool ownership'
 else:assert all(p['enabled']=='false' for p in pools),'R0 pool'
 summary,=records(text,'summary' if c==1 else 'summary_concurrent')
 alltimes=sorted(int(x['latency_us']) for x in samples)
 assert int(summary['median_us' if c==1 else 'median_request_us'])==alltimes[len(alltimes)//2],'printed median'
 effective,=records(text,'mode_effective');assert effective==dict(one=str(n*c),packed_greedy='0',packed_contiguous='0',direct='0'),'effective mode'
 batches=records(text,'batch')
 if c>1:assert len(batches)==n and sorted(int(x['iteration']) for x in batches)==list(range(1,n+1)),'batch coverage'
 times=[int(x['latency_us']) for x in samples if int(x['iteration' if c==1 else 'batch'])>5]
 return {'identity':before,'final_pool':final,'samples':samples,'startup_samples_us':[int(x['latency_us']) for x in samples if int(x['iteration' if c==1 else 'batch'])<=5],'steady_samples_us':times,'steady_median_us':statistics.median(times),'steady_p95_us':sorted(times)[math.ceil(.95*len(times))-1],'summary':summary,'batch_wall_us':[int(x['wall_us']) for x in batches]}

def main(root):
 root=pathlib.Path(root); manifest=json.loads((root/'manifest.json').read_text());index=json.loads((root/'run-index.json').read_text());known=json.loads((root/'known-pattern-hashes.json').read_text());issues=[];blocks={};identities={}; measurements=[r for r in index if r['phase']=='measure']; diagnostics=[r for r in index if r['phase']=='diagnostic'];rows=[]
 expected_slots={(s,c,b,v) for s in [1,8,64] for c in [1,4] for b in range(10) for v in ['R0','R1']}
 observed=[]
 for r in index:
  if r['exit_code']!=0:issues.append(r['name']+': exit '+str(r['exit_code']))
 for r in measurements+diagnostics:
  try:
   assert r['exit_code']==0,'process exit'
   t=(root/(r['name']+'.stdout')).read_text();p=validate(r,t,known)
   identkey=(r['size_mib'],r['object_index']);assert identities.setdefault(identkey,p['identity'])==p['identity'],'identity differs across processes'
   if r['phase']=='diagnostic':continue
   key=(r['size_mib'],r['concurrency'],r['pair'],r['variant']);observed.append(key)
   assert r['object_index']==r['pair']%3,'object_rotation'
   order=['R0','R1'] if r['pair']%2==0 else ['R1','R0'];assert r['variant']==order[r['order_position']],'pair_order'
   blocks[key]=dict(name=r['name'],**p)
   for x in p['samples']:
    batch=int(x['iteration' if r['concurrency']==1 else 'batch'])
    rows.append(dict(size_mib=r['size_mib'],concurrency=r['concurrency'],pair=r['pair'],variant=r['variant'],object_index=r['object_index'],batch=batch,worker=int(x.get('worker',0)),phase='startup' if batch<=5 else 'steady',latency_us=int(x['latency_us']),xxh3=x['xxh3']))
  except Exception as e:issues.append(r['name']+': '+repr(e))
 if set(observed)!=expected_slots or len(observed)!=120:issues.append('missing/duplicated primary slots')
 if len([r for r in index if r['phase']=='seed'])!=9:issues.append('expected nine seed processes')
 if len(diagnostics)!=6:issues.append('six diagnostics incomplete')
 if manifest.get('state')!='COMPLETED' or not manifest.get('binaries_unchanged'):issues.append('final manifest incomplete or binaries changed')
 result={'state':'VALIDATED_LOCALLY_PENDING_INDEPENDENT_REVIEW' if not issues else 'INVALID_OR_INCOMPLETE','issues':issues,'scheduled_measurement_processes':120,'observed_measurement_processes':len(measurements),'total_measured_requests':sum(len(records((root/(r['name']+'.stdout')).read_text(),'sample')) for r in measurements),'steady_requests_expected':9000,'analysis_unit':'independent paired process block; requests are subsamples','primary_metric':'ordinary median of 30*C steady per-request latencies; geometric mean paired R0/R1','scope':'SKV6 to SKV5, single physical HCA per worker, one route, 512KiB stripes, forced application storage path, buffered tier_a file reads, warm OS page cache, recovery disabled','timing_boundary':'request excludes subsequent full-pattern hashing; concurrency4 batch wall/CPU includes coordination and hashing; concurrency1 CPU narrower','adoption':'opt-in/default off; no inference, production, p99 or dual-HCA claim','cells':[],'identities':{str(k):v for k,v in identities.items()}}
 for s in [1,8,64]:
  for c in [1,4]:
   keys=[(s,c,b,v) for b in range(10) for v in ['R0','R1']]
   if any(k not in blocks for k in keys):result['cells'].append({'size_mib':s,'concurrency':c,'state':'INCOMPLETE'});continue
   pairblocks=[{v:blocks[(s,c,b,v)] for v in ['R0','R1']} for b in range(10)]
   ratios=[p['R0']['steady_median_us']/p['R1']['steady_median_us'] for p in pairblocks];logs=list(map(math.log,ratios));mean=statistics.mean(logs);half=2.2621571627409915*statistics.stdev(logs)/math.sqrt(10)
   rng=random.Random(20261029+s*10+c);boot=sorted(math.exp(statistics.mean(logs[rng.randrange(10)] for _ in range(10))) for _ in range(20000))
   cell={'size_mib':s,'concurrency':c,'independent_pairs':10,'ratio_R0_over_R1':gm(ratios),'bootstrap_95pct':[boot[499],boot[19499]],'logratio_t95_df9':[math.exp(mean-half),math.exp(mean+half)],'R1_slower_blocks':sum(r<1 for r in ratios),'paired_ratios':ratios,'latency_reduction_percent':100*(1-1/gm(ratios)),'p95_ratio_descriptive':gm([p['R0']['steady_p95_us']/p['R1']['steady_p95_us'] for p in pairblocks]),'R0_medians_us':[p['R0']['steady_median_us'] for p in pairblocks],'R1_medians_us':[p['R1']['steady_median_us'] for p in pairblocks],'R0_p95_us':[p['R0']['steady_p95_us'] for p in pairblocks],'R1_p95_us':[p['R1']['steady_p95_us'] for p in pairblocks],'blocks':pairblocks}
   result['cells'].append(cell)
 result['server_profile']='release';result['deployment_selection']='main deployment result regardless of direction or magnitude; debug separately retained';result['storage_interpretation']='tier_a4 workers; pointer read via std::fs::File::open and read_exact, buffered; forced path bypasses application L1 but does not prove physical media access; OS page cache not cleared';result['cross_profile_boundary']='debug/release have different build features and source snapshot; no sole-cause compiler or guard speedup attribution'
 (root/'analysis.json').write_text(json.dumps(result,indent=2)+'\n')
 with (root/'samples.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else []);w.writeheader();w.writerows(rows)
 (root/'ppt-cell-results.json').write_text(json.dumps({**{k:v for k,v in result.items() if k not in ['cells','identities']},'cells':[{k:v for k,v in c.items() if k!='blocks'} for c in result['cells']]},indent=2)+'\n')
 print(json.dumps({'state':result['state'],'issues':issues,'cells':[{k:v for k,v in c.items() if k not in ['blocks','R0_medians_us','R1_medians_us','R0_p95_us','R1_p95_us','paired_ratios']} for c in result['cells']]},indent=2))
if __name__=='__main__':main(sys.argv[1])
