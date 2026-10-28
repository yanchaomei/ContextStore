import pathlib,subprocess,json,os,hashlib,datetime
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029');root=base/'matrix512k';env={**os.environ,'LD_LIBRARY_PATH':str(base/'lib')}
cli=[str(base/'bin/redis-cli'),'-p','26579','--raw']
keys=subprocess.check_output(cli+['--scan'],env=env,text=True).splitlines();objects=[]
for key in keys:
 if ':block_meta:' not in key:continue
 meta=json.loads(subprocess.check_output(cli+['get',key],env=env,text=True))
 if 'wave2-matrix512k-smoke-' not in key:continue
 mib=meta['size']//1048576;assert mib in [1,64]
 stripe=meta['striping'];count=len(stripe['chunk_paths']);assert stripe['chunk_size']==524288 and count==mib*2 and len(stripe['chunk_checksums'])==count
 paths=stripe['chunk_paths'];sizes=[pathlib.Path(p).stat().st_size for p in paths];assert sum(sizes)==mib*1048576 and all(n==524288 for n in sizes)
 objects.append({'key':key,'size_mib':mib,'size':meta['size'],'is_striped_storage_metadata':True,'stripe_count':count,'stripe_bytes':stripe['chunk_size'],'content_etag':meta['content_etag'],'object_generation':meta['object_generation'],'layout_version':meta['layout_version'],'chunk_files_verified':len(sizes),'metadata':meta})
assert sorted(x['size_mib'] for x in objects)==[1,64]
pid=int((root/'evidence/server.pid').read_text());proc=pathlib.Path('/proc')/str(pid);actualenv=dict(x.split('=',1) for x in (proc/'environ').read_text().split('\0') if x)
assert actualenv['CS_RDMA_SLAB_MB']=='512'
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'objects':objects,'server_pid':pid,'start_ticks':(proc/'stat').read_text().split()[21],'actual_environment':actualenv,'actual_environment_sha256':hashlib.sha256((proc/'environ').read_bytes()).hexdigest(),'config_sha256':hashlib.sha256((root/'server.toml').read_bytes()).hexdigest(),'server_exe_sha256':hashlib.sha256((proc/'exe').read_bytes()).hexdigest()}
(root/'evidence/layout-metadata.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
