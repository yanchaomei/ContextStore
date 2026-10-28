import pathlib,subprocess,json,hashlib,datetime,time
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_validation_wave2_20261029');root=base/'matrix512k/smoke-attempt2';root.mkdir()
(root/'client.env').write_bytes((base/'client.env').read_bytes())
expected={'cs-bench':'904dd6cbf4eb096de067b9f1c2252bdce785b119ca40b57f52c7608cdf218b44','cs-rail-read-bench':'6d90ed413283a513d32cb3b70428ba47cf3c7311ab47752bdc810db726bf3ca0'}
actual={name:hashlib.sha256((base/'bin'/name).read_bytes()).hexdigest() for name in expected};assert actual==expected
runs=[]
for mib in [1,64]:
 prefix=f'wave2-matrix512k-smoke-{mib}-'
 commands=[('seed',[str(base/'bin/cs-bench'),'--endpoint','http://10.0.0.65:25551','--layers','1','--layer-mb',str(mib),'--concurrency','1','--combined','--combined-count','1','--stream','--stream-chunk-mb','2','--prefix-base',prefix,'--only-put']),('read',[str(base/'bin/cs-rail-read-bench'),'--coordinator','http://10.0.0.65:25551','--namespace','rust-bench','--object-key',prefix+'0/__combined__','--rail','single,mlx5_1,10.0.0.65:25553,10.0.0.65:25553,1,3','--environment','physical','--warmup','0','--iterations','1','--concurrency','1','--verify-pattern','--connection-pool-size','0'])]
 for phase,argv in commands:
  started=datetime.datetime.now(datetime.timezone.utc).isoformat()
  with open(root/f'{mib}-{phase}.stdout','wb') as out,open(root/f'{mib}-{phase}.stderr','wb') as err:p=subprocess.run(argv,cwd=root,stdout=out,stderr=err,timeout=180)
  runs.append({'size_mib':mib,'phase':phase,'argv':argv,'started':started,'finished':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit_code':p.returncode})
  (root/'smoke-runs.json').write_text(json.dumps(runs,indent=2)+'\n');assert p.returncode==0,(mib,phase,p.returncode)
receipt={'state':'ONE_READ_EACH_1MIB_64MIB_PASSED','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'bin_sha256':actual,'bin_sha256_after':{name:hashlib.sha256((base/'bin'/name).read_bytes()).hexdigest() for name in expected},'runs':runs}
assert receipt['bin_sha256_after']==expected
(root/'layout-smoke-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
