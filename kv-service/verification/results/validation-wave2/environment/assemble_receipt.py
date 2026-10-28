from pathlib import Path
import json,hashlib,datetime
root=Path(__file__).resolve().parent
def j(name):return json.loads((root/name).read_text())
def cases(folder):
 out={}
 for p in sorted(folder.glob('*.exit_code')):
  name=p.name[:-10];log=folder/(name+'.log')
  out[name]={'exit_code':int(p.read_text()),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'log':str(log.relative_to(root))}
 return out
meta=j('rxe-small-metadata.json');obj=json.loads(next(x['value'] for x in meta['records'] if ':block_meta:' in x['key']))
assert obj['size']==1048576 and obj['striping']['chunk_size']==262144 and len(obj['striping']['chunk_paths'])==4
sourcecheck=(root/'physical-baseline/source-verification.log').read_text().splitlines()
assert sourcecheck and all(x.endswith(': OK') for x in sourcecheck)
physical=j('physical-final-readback.json');rxe=j('rxe-final-readback.json')
for rec in [*physical['fixtures'],*rxe['fixtures']]:
 s=rec['processes']['server'];assert s['exe_sha256']=='0e9c081f15f809839684bab64e24b75c547ec1ce4e0e1a4a2060f6146c76c87c'
 if rec['root'].endswith('/small'):assert s['environment']['CS_RDMA_CQ_TIMEOUT_MS']=='1000'
 else:assert 'CS_RDMA_CQ_TIMEOUT_MS' not in s['environment']
r={
 'state':'READY_BASELINES_PASSED_AND_SMALL_RXE_SMOKE_PASSED',
 'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'requested_candidate_repository_head':'1c3ee135d19fece67507b51acd1fc51ae9541208',
 'fixture_source_commit':'3fe4b26d72fffbe3a3d95236b858ab2ed0d78a6b',
 'baseline_source_hashes_verified':len(sourcecheck),
 'server_binary_sha256':'0e9c081f15f809839684bab64e24b75c547ec1ce4e0e1a4a2060f6146c76c87c',
 'baseline_test_binaries':{'rdma_e2e':'a458392eeaff6e5b9d64a9fcec2f08fb1832fa16253be213d79c1f9da6fa5957','rail_read_e2e':'06a2a6657c14b1099dead47a8dcb120efaa9676c880f09a302c483a813a0ee26'},
 'physical':{'state':'SINGLE_HCA_READY','client_env':'SKV_2:/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/wave2/provider-client.env','grpc':'http://10.0.0.64:25351','rdma':'10.0.0.64:25353','device':'mlx5_1','port':1,'gid_index':3,'object_mib':16,'stripe_bytes':4194304,'server_cq_timeout_ms':30000,'readback':physical,'baseline_cases':cases(root/'physical-baseline'),'dual_same_path_expected_rejection':'InvalidPlacement: rail IDs, listeners, and local ports must identify distinct paths'},
 'rxe':{'state':'DUAL_RXE_READY','host':'SKV_2','wrapper':'bash /home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/vm-rxe/guest_ssh.sh server|client','client_env':'/home/railtest/cswave1/wave2/client.env','grpc':'http://10.81.1.1:25351','rdma':['10.81.1.1:25353','10.82.1.1:25354'],'object_mib':16,'stripe_bytes':4194304,'server_cq_timeout_ms':30000,'baseline_cases':cases(root/'rxe-client-evidence/evidence/baseline'),'readback':rxe,'qemu':j('qemu_identity.json'),'server_provider':j('rxe-server-restore.json'),'client_provider':j('rxe-client-restore.json')},
 'small_rxe':{'state':'SMOKE_PASSED','client_env':'/home/railtest/cswave1/wave2/small/client.env','server_config':'/home/railtest/cswave1/wave2/small/server.toml','grpc':'http://10.81.1.1:25451','rdma':['10.81.1.1:25453','10.82.1.1:25454'],'redis_port':26479,'object_mib':1,'stripe_bytes':262144,'stripe_count':4,'server_cq_timeout_ms':1000,'timeout_verified_in_proc':True,'smoke':cases(root/'rxe-small-client-evidence/evidence'),'is_striped_evidence':'Passed single_real_rail_restores_verified_object asserts lookup.descriptor.is_striped','stripe_count_evidence':'Redis metadata has exactly 4 chunk_paths and 4 checksums for same seed','seed_metadata':obj,'server':j('rxe-small-server-restore.json'),'client':j('rxe-small-client-restore.json')},
 'dual_physical_hardware':{'state':'HARDWARE_UNAVAILABLE','observation':'All 7 nodes: mlx5_1/port1 ACTIVE, mlx5_0/port1 DOWN. No existing dual active physical paths. No host changes.','topology':{f'SKV_{n}':j(f'skv{n}-topology.json') for n in range(1,8)}},
 'preserved':['Wave1 source/log/config/snapshot files unchanged','Original VM UUIDs and overlay hashes checked before start','New VM logs under wave2','16MiB fixture retains default 30s server CQ timeout','Host networking/drivers/groups unchanged; no old SKV1 VM touched'],
 'limitations':['RXE evidence is software RDMA correctness, not physical dual-HCA performance','Concurrent physical/RXE work cannot support comparative performance claims','Environment task ran only finite baseline/smoke checks; long soak and 1000-cycle runs are parent-owned'],
 'setup_notes':['First provider attempt failed before launch because remote Python3.8 lacks dict union; compatible dict expansion used','Early guest SSH timed out during boot, then hostname/RXE/baselines passed','QEMU pid/exe metadata read with exact-path sudo; permissions unchanged','Guest optional librdmacm1 package missing is recorded; required ibverbs providers present and baseline RDMA passed']}
(root/'environment-ready-receipt.json').write_text(json.dumps(r,indent=2)+'\n')
manifest={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and p.name!='environment-file-hashes.json'}
(root/'environment-file-hashes.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Ready receipt created; hashed files:',len(manifest))
print('Physical cases:',{k:v['exit_code'] for k,v in r['physical']['baseline_cases'].items()})
print('RXE cases:',{k:v['exit_code'] for k,v in r['rxe']['baseline_cases'].items()})
print('Small RXE:',{k:v['exit_code'] for k,v in r['small_rxe']['smoke'].items()})
