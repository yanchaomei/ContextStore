import pathlib,json,statistics,re,sys,gzip,hashlib
P=pathlib.Path(__file__).resolve().parent;R=P/'client-evidence/performance-20261029-v2'
rows=[]
for s in [1,8,64]:
 for v in ['R0','R1']:
  p=R/f'diagnostic-{s}MiB-{v}.stderr';text=p.read_text();stages={}
  for kind in ['RAIL_TRANSPORT_TIMING','RAIL_STAGED_TIMING','RAIL_OUTER_TIMING']:
   rec=[dict(f.split('=',1) for f in l.split()[1:] if '=' in f) for l in text.splitlines() if l.startswith(kind+' ')]
   assert len(rec)==6,(p,kind,len(rec))
   metric={}
   for k in rec[0]:
    if k.endswith('_us'):
     values=[int(r[k]) for r in rec];metric[k]={'first_read':values[0],'next_five_median':statistics.median(values[1:]),'next_five_values':values[1:]}
   stages[kind]=metric
  rows.append({'size_mib':s,'variant':v,'stages':stages})
r={'state':'DIAGNOSTIC_ONLY','scope':'6 separate traced serial processes, 6 reads per process; first read versus next five; no headline latency comparison','connect_us_boundary':'transport connect_us includes pool lease acquire; R1 subsequent reads reuse; registration and teardown are separate','cells':rows}
(P/'phase-diagnostics.json').write_text(json.dumps(r,indent=2)+'\n')
log=gzip.decompress((P/'server-matrix-window.log.gz').read_bytes()).decode();clean=re.sub(r'\x1b\[[0-9;]*m','',log);lines=clean.splitlines();fallback=[l for l in lines if 'fallback' in l.lower() or 'WARN' in l or 'ERROR' in l]
r={'scope':'server log byte window includes v2 primary and traced diagnostics only; forced disk read, warm OS page cache; independent from client primary timing','server_detail_lines':sum('RDMA_SUBSET_DETAIL' in l for l in lines),'connection_lines':sum('RDMA client connected' in l for l in lines),'fallback_warn_error_count':len(fallback),'fallback_warn_error_lines':fallback}
(P/'server-log-observations.json').write_text(json.dumps(r,indent=2)+'\n')
print(json.dumps({'diagnostic_cells':len(rows),'server_detail_lines':r['server_detail_lines'],'fallback_warn_error_count':r['fallback_warn_error_count']},indent=2))
