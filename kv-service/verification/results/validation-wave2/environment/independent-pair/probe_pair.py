import os,pathlib,subprocess,socket,time,json,datetime,shutil
root=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029')
def command(args):
 p=subprocess.run(args,text=True,capture_output=True);return {'code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
def cpu():return list(map(int,pathlib.Path('/proc/stat').read_text().splitlines()[0].split()[1:]))
a=cpu();time.sleep(1);b=cpu();d=[y-x for x,y in zip(a,b)];idle=(d[3]+d[4])/sum(d)
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hostname':socket.gethostname(),'kernel':command(['uname','-a']),'cpu_count':os.cpu_count(),'loadavg':os.getloadavg(),'cpu_idle_pct_1s':round(idle*100,2),'meminfo':pathlib.Path('/proc/meminfo').read_text(),'disk':dict(zip(['total','used','free'],shutil.disk_usage('/home/kvgroup/chaomei'))),'root_exists':root.exists(),'ports':command(['ss','-lntup']),'glibc':command(['getconf','GNU_LIBC_VERSION']),'packages':command(['dpkg-query','-W','rdma-core','ibverbs-providers','libibverbs1','redis-server']),'rdma':command(['rdma','link','show']),'ip':command(['ip','-br','addr']),'top_cpu_processes':command(['ps','-eo','pid,user,pcpu,pmem,comm','--sort=-pcpu']),'gids':{}}
r['top_cpu_processes']['stdout']='\n'.join(r['top_cpu_processes']['stdout'].splitlines()[:15])
for dev in ['mlx5_1']:
 p=pathlib.Path('/sys/class/infiniband')/dev
 r['gids'][dev]={'pci':str((p/'device').resolve()),'numa':(p/'device/numa_node').read_text().strip(),'gid3':(p/'ports/1/gids/3').read_text().strip(),'gid3_type':(p/'ports/1/gid_attrs/types/3').read_text().strip()}
if socket.gethostname()=='skv-node6':r['reach_server']=command(['ping','-n','-c','3','-W','2','10.0.0.65'])
print(json.dumps(r,indent=2))
