#!/usr/bin/env python3
import pathlib,json,hashlib,os,socket,datetime,subprocess
root=pathlib.Path('/home/railtest/cswave1')
data={'hostname':socket.gethostname(),'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'kernel':subprocess.check_output(['uname','-a'],text=True).strip(),'bin_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'bin').iterdir() if p.is_file()},'rdma_links':subprocess.check_output(['rdma','link','show'],text=True),'ip_addresses':subprocess.check_output(['ip','-br','addr'],text=True),'gid_index':1}
for name in ['server.toml','client.env','guest_server_run.sh','guest_run_baseline.sh']:
    p=root/name
    if p.exists():data[name+'_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
pidfile=root/'evidence/server.pid'
if pidfile.exists():
    pid=int(pidfile.read_text());proc=pathlib.Path('/proc')/str(pid)
    env={}
    for field in (proc/'environ').read_bytes().split(b'\0'):
        if b'=' in field:
            key,value=field.split(b'=',1)
            if key.startswith(b'CS_'):env[key.decode()]=value.decode()
    data['server']={'pid':pid,'start_ticks':(proc/'stat').read_text().split()[21],'exe':os.readlink(proc/'exe'),'exe_sha256':hashlib.sha256((proc/'exe').read_bytes()).hexdigest(),'environment':env}
    with socket.create_connection(('127.0.0.1',26379),2) as s:
        s.sendall(b'*1\r\n$4\r\nPING\r\n');data['private_redis_ping']=s.recv(32).decode().strip()
(root/'evidence/active_guest.json').write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps(data,indent=2))
