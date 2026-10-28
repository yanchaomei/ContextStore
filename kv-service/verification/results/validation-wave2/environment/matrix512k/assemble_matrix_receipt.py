import pathlib,json,hashlib,datetime,re
root=pathlib.Path(__file__).resolve().parent
def j(name):return json.loads((root/name).read_text())
server=j('server-startup.json');meta=j('server-layout-metadata.json');client=j('client-layout-smoke.json')
assert server['processes']['server']['pid']==meta['server_pid']
assert meta['actual_environment']['CS_RDMA_SLAB_MB']=='512'
assert meta['actual_environment']['CS_FORCE_DISK_READ']=='1'
assert meta['config_sha256']==server['config_sha256']
checks=[]
for mib,count,expected in [(1,2,'c061e1dcd62f6c77'),(64,128,'a0a4cbfa5cad46af')]:
 p=root/f'client-evidence/smoke-attempt2/{mib}-read.stdout';s=p.read_text();lines=s.splitlines()
 before=next(x for x in lines if x.startswith('object_identity,'));after=next(x for x in lines if x.startswith('object_identity_after,'));assert before.split(',',1)[1]==after.split(',',1)[1]
 sample=next(x for x in lines if x.startswith('sample,'));pattern=next(x for x in lines if x.startswith('expected_pattern,'));assert f'xxh3={expected}' in sample and f'xxh3={expected}' in pattern
 assert f'stripe_count={count},chunk_size=524288' in before
 stat=next(x for x in lines if x.startswith('rail_stats,'));assert 'reads_ok=1,reads_err=0' in stat and 'inflight_requests=0' in stat and 'registered_bytes_reserved=0' in stat
 checks.append({'object_mib':mib,'is_striped':True,'is_striped_evidence':'successful multi-stripe plan plus nonempty striping metadata; descriptor is_striped=false would plan exactly one stripe and reject the multi-chunk placement','descriptor_stripe_count':count,'chunk_size':524288,'expected_xxh3':expected,'read_xxh3':expected,'identity_before_after_equal':True,'reads_ok':1,'reads_err':0,'retained_inflight_requests':0,'registered_bytes_reserved':0,'log_sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
source=pathlib.Path('outputs/implementation-wave1-20261029/campaign/pilot-source-freeze-v3.json')
r={'state':'MATRIX512K_READY_1MIB_AND_64MIB_CORRECTNESS_PASSED','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'server_host':'SKV_5','client_host':'SKV_6','server_root':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/matrix512k','server_config':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/matrix512k/server.toml','client_env':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/matrix512k/client.env','binaries':{'seeder':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/bin/cs-bench','benchmark':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/bin/cs-rail-read-bench'},'binary_sha256':client['bin_sha256'],'binary_source_freeze':json.loads(source.read_text()),'server_source_commit':'3fe4b26d72fffbe3a3d95236b858ab2ed0d78a6b','server_sha256':meta['server_exe_sha256'],'grpc':'http://10.0.0.65:25551','rdma':'10.0.0.65:25553','redis_port':26579,'device_each_side':'mlx5_1','port':1,'gid':3,'single_physical_path':True,'striping_threshold':524288,'striping_chunk_size':524288,'slab_mib':512,'force_disk_read':True,'server_cq_timeout_ms':30000,'matrix_target_sizes_mib':[1,8,64],'server_startup':server,'actual_server_readback':meta,'client_smoke':client,'verification':checks,'old_fixture_stop':j('old-fixture-stop-receipt.json'),'preservation':['Old4MiB-stripe baseline logs/config/data remain at original root','OldRedis SHUTDOWN SAVE snapshot and pre/post-stop evidence archives retained in matrix512k-transition','Fresh512KiB server config,data,Redis and evidence isolated under matrix512k','Original SKV2/SKV4 and RXE fixtures not touched','No production source edits, cargo builds, package installs, or host driver changes'],'scope':'Two bounded correctness reads only, not a performance result; no test traffic after smoke. New service remains running for parent R0/R1 matrix.','known_failed_attempts':['Initial stop guard rejected Redis cwd root/redis; exactPID,startticks,exe hash matched; corrected guard and rechecked before stopping','Initial bare bind preflight rejected Redis TIME_WAIT with no listener; changed probe to SO_REUSEADDR','First1MiB seed attempt while service absent returned1 before any read; logs retained in client-evidence/1-seed.*; successful rerun isolated in smoke-attempt2']}
(root/'matrix512k-ready-receipt.json').write_text(json.dumps(r,indent=2)+'\n')
manifest={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and p.name!='matrix512k-file-hashes.json'}
(root/'matrix512k-file-hashes.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(r['state'],len(manifest),'files')
print('server PID/startticks:',meta['server_pid'],meta['start_ticks'])
print('config sha:',meta['config_sha256'])
