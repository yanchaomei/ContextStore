import pathlib,json,hashlib,tarfile,datetime
root=pathlib.Path(__file__).resolve().parent
def j(name):return json.loads((root/name).read_text())
server=j('server-startup.json');meta=j('server-layout-metadata.json');client=j('client-layout-smoke.json');freeze=j('server-build-source-evidence/release-freeze.json');manifest=j('server-build-source-evidence/green-source-sha256.json')
assert server['processes']['server']['pid']==meta['server_pid']
assert meta['server_exe_sha256']==freeze['binary_sha256']=='f6607705f29e30bc293ada3f9e64422cda8e0dadd8dc2996d02cec8ce8d3f0d8'
assert meta['actual_environment']['CS_RDMA_SLAB_MB']=='512' and meta['actual_environment']['CS_FORCE_DISK_READ']=='1'
assert meta['config_sha256']==server['config_sha256']
config=(root/'server.toml').read_text()
assert 'kind = "tier_a"' in config and 'thread_pool_size = 4' in config and 'striping_threshold = 524288' in config and 'striping_chunk_size = 524288' in config
checked=0
with tarfile.open(root/'server-build-source-evidence/green-source.tar.gz') as t:
 for name,digest in manifest.items():assert hashlib.sha256(t.extractfile(name).read()).hexdigest()==digest,name;checked+=1
assert checked==65
checks=[]
for mib,count,expected in [(1,2,'c061e1dcd62f6c77'),(64,128,'a0a4cbfa5cad46af')]:
 p=root/f'client-evidence/smoke/{mib}-read.stdout';lines=p.read_text().splitlines()
 before=next(x for x in lines if x.startswith('object_identity,'));after=next(x for x in lines if x.startswith('object_identity_after,'));assert before.split(',',1)[1]==after.split(',',1)[1]
 sample=next(x for x in lines if x.startswith('sample,'));pattern=next(x for x in lines if x.startswith('expected_pattern,'));assert f'xxh3={expected}' in sample and f'xxh3={expected}' in pattern
 assert f'stripe_count={count},chunk_size=524288' in before
 stat=next(x for x in lines if x.startswith('rail_stats,'));assert 'reads_ok=1,reads_err=0' in stat and 'inflight_requests=0' in stat and 'registered_bytes_reserved=0' in stat
 checks.append({'size_mib':mib,'is_striped':True,'stripe_count':count,'stripe_bytes':524288,'hash_expected':expected,'hash_read':expected,'identity_before_after_equal':True,'reads_ok':1,'reads_err':0,'inflight_requests_after':0,'registered_bytes_after':0,'log_sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
amend=pathlib.Path('outputs/validation-wave2-20261029/release-profile-amendment.json')
r={'state':'RELEASE_MATRIX512K_READY_FINITE_CORRECTNESS_PASSED','frozen_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'amendment':json.loads(amend.read_text()),'amendment_sha256':hashlib.sha256(amend.read_bytes()).hexdigest(),'server_host':'SKV_5','client_host':'SKV_6','server_root':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/release-matrix512k','server_config':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/release-matrix512k/server.toml','server_binary':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/release-matrix512k/bin/contextstore-server','client_env':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/release-matrix512k/client.env','client_binaries':{'seeder':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/bin/cs-bench','benchmark':'/home/kvgroup/chaomei/contextstore_validation_wave2_20261029/bin/cs-rail-read-bench'},'client_binary_sha256':client['bin_sha256'],'server_binary_source_freeze':freeze,'source_archive_files_independently_verified':checked,'server_profile':'default release make build','compiled_features':['io-uring','rdma','metrics'],'runtime_io_executor':'tier_a','thread_pool_size':4,'slab_mib':512,'striping_threshold_bytes':524288,'striping_chunk_bytes':524288,'force_disk_read':True,'server_cq_timeout_ms':30000,'grpc':'http://10.0.0.65:25551','rdma':'10.0.0.65:25553','device_each_side':'mlx5_1','port':1,'gid_index':3,'redis_port':26579,'redis_key_prefix':'contextstore:wave2-release-matrix512k-20261029:','isolated_from_debug_data':True,'fresh_campaign_keys_available':True,'existing_objects_only_smoke_sizes':[1,64],'server_startup':server,'server_actual_readback':meta,'client_smoke':client,'correctness_checks':checks,'resource_preflight':{'SKV5':j('skv5-probe.json'),'SKV6':j('skv6-probe.json')},'performance_state':'No performance matrix run by environment task; smoke traffic stopped; ready for performance agent to seed9freshkeys and run frozen120primary+6diagnostic protocol','reporting_rule':'Release matrix is main deployment result regardless of effect; previous debug matrix retained separately. No debug-versus-release absolute speedup attribution.','storage_scope':'tier_a buffered pointer path; CS_FORCE_DISK_READ bypasses application L1 but does not establish cold physical-device I/O','preservation':['Olddebug config,data,Redis snapshots,results and archives unchanged','No source edits or builds by environment task','Only explicit frozen artifact files read from SKV2; no SKV2/SKV4 service or RXE action','No oldFFI tasks run; all measured objects striped']}
(root/'release-matrix512k-ready-receipt.json').write_text(json.dumps(r,indent=2)+'\n')
hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and p.name!='release-matrix512k-file-hashes.json'}
(root/'release-matrix512k-file-hashes.json').write_text(json.dumps(hashes,indent=2)+'\n')
print(r['state'],len(hashes),'files');print('server',meta['server_pid'],meta['start_ticks']);print('configsha',meta['config_sha256'])
