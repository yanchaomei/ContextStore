import subprocess,json,pathlib,hashlib,datetime
root=pathlib.Path('/home/railtest/cswave1/wave2/small')
keys=subprocess.check_output(['redis-cli','-p','26479','--scan'],text=True).splitlines()
records=[]
for key in keys:
 kind=subprocess.check_output(['redis-cli','-p','26479','type',key],text=True).strip()
 if kind=='string':
  value=subprocess.check_output(['redis-cli','-p','26479','--raw','get',key],text=True)
 elif kind=='hash':
  value=subprocess.check_output(['redis-cli','-p','26479','--raw','hgetall',key],text=True)
 else: value='unsupported type '+kind
 records.append({'key':key,'type':kind,'value':value})
pid=int((root/'evidence/server.pid').read_text())
proc=pathlib.Path('/proc')/str(pid)
env=dict(x.split('=',1) for x in (proc/'environ').read_text().split('\0') if x)
assert env['CS_RDMA_CQ_TIMEOUT_MS']=='1000'
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'server_pid':pid,'server_start_ticks':(proc/'stat').read_text().split()[21],'server_CS_environment':{k:v for k,v in env.items() if k.startswith('CS_')},'records':records}
(root/'evidence/metadata-readback.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
