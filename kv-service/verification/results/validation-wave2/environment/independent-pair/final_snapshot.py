import pathlib,subprocess,os,socket,json,hashlib,datetime,shutil
root=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029')
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'hostname':socket.gethostname(),'root':str(root),'hashes':{},'processes':{},'free_disk_bytes':shutil.disk_usage(root).free,'loadavg':os.getloadavg(),'kernel':subprocess.check_output(['uname','-a'],text=True),'glibc':subprocess.check_output(['getconf','GNU_LIBC_VERSION'],text=True),'rdma':subprocess.check_output(['rdma','link','show'],text=True)}
for p in list((root/'bin').glob('*'))+list((root/'lib').glob('*'))+[root/'client.env',root/'server.toml']:
 if p.is_file():r['hashes'][str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
for label in ['server','redis']:
 pidfile=root/'evidence'/f'{label}.pid'
 if not pidfile.exists():continue
 pid=int(pidfile.read_text());proc=pathlib.Path('/proc')/str(pid)
 env=dict(x.split('=',1) for x in (proc/'environ').read_text().split('\0') if x)
 r['processes'][label]={'pid':pid,'start_ticks':(proc/'stat').read_text().split()[21],'command':(proc/'cmdline').read_text().replace('\0',' ').strip(),'cwd':os.readlink(proc/'cwd'),'exe':os.readlink(proc/'exe'),'exe_sha256':hashlib.sha256((proc/'exe').read_bytes()).hexdigest(),'environment':env,'environment_sha256':hashlib.sha256((proc/'environ').read_bytes()).hexdigest()}
(root/'evidence/final_snapshot.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
