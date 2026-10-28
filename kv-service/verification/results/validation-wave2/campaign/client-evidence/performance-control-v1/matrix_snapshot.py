#!/usr/bin/env python3
import datetime, hashlib, json, os, pathlib, subprocess, sys
B=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for c in iter(lambda:f.read(1048576),b''):h.update(c)
 return h.hexdigest()
def cmd(v):
 p=subprocess.run(v,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30)
 return dict(argv=v,exit_code=p.returncode,stdout=p.stdout,stderr=p.stderr)
def snap(role):
 d={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'role':role,'hostname':os.uname().nodename,'kernel':cmd(['uname','-a']),'load':pathlib.Path('/proc/loadavg').read_text(),'meminfo':pathlib.Path('/proc/meminfo').read_text(),'df':cmd(['df','-k',str(B)]),'cpu':cmd(['lscpu']),'rdma':cmd(['rdma','link','show']),'top':cmd(['ps','-eo','pid,ppid,comm,pcpu,pmem,rss','--sort=-pcpu']),'binaries':{}}
 for n in ['cs-bench','cs-rail-read-bench'] if role=='client' else ['contextstore-server']:
  d['binaries'][n]=sha(B/'bin'/n)
 for name,p in [('client_env',B/'matrix512k/client.env')] if role=='client' else [('server_config',B/'matrix512k/server.toml')]:
  d[name]={'sha256':sha(p),'content':p.read_text()}
 d['fabric']={}
 for sub in ['state','phys_state','rate','gids/3','gid_attrs/types/3','gid_attrs/ndevs/3']:
  p=pathlib.Path('/sys/class/infiniband/mlx5_1/ports/1')/sub
  d['fabric'][sub]=p.read_text().strip()
 if role=='server':
  pid=1882561;p=pathlib.Path('/proc')/str(pid)
  env=dict(x.split('=',1) for x in (p/'environ').read_text().split('\0') if '=' in x)
  d['server']={'pid':pid,'start_ticks':(p/'stat').read_text().split(') ')[1].split()[19],'exe':os.readlink(p/'exe'),'exe_sha256':sha(p/'exe'),'command':(p/'cmdline').read_text().split('\0'),'environment':{k:v for k,v in env.items() if k.startswith('CS_') or k in ('PATH','LANG','LD_LIBRARY_PATH')},'status':(p/'status').read_text(),'fd_count':len(list((p/'fd').iterdir()))}
  log=B/'matrix512k/evidence/server.log'
  d['server_log']={'bytes':log.stat().st_size,'sha256':sha(log)}
 return d
if __name__=='__main__':print(json.dumps(snap(sys.argv[1]),indent=2))
