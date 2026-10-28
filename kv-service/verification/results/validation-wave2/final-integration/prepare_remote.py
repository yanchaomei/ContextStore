import pathlib,subprocess,json,hashlib,tarfile,datetime,shutil,os
base=pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029');root=base/'wave2/final-integration';e=root/'evidence'
manifest=json.loads((root/'source-input-manifest.json').read_text());freeze=json.loads((root/'local-source-freeze.json').read_text())
assert hashlib.sha256((root/'source-inputs.tar.gz').read_bytes()).hexdigest()==freeze['source_archive_sha256']
with tarfile.open(root/'source-inputs.tar.gz') as t:t.extractall(root/'src')
checks={name:hashlib.sha256((root/'src'/name).read_bytes()).hexdigest()==info['sha256'] for name,info in manifest.items()};assert all(checks.values())
active=subprocess.run(['pgrep','-a','cargo'],text=True,capture_output=True);assert active.returncode==1,active.stdout
cache=base/'wave2/descriptor-guard/target';assert cache.is_dir() and not (root/'target').exists()
assert shutil.disk_usage(root).free>25*1024**3
start=datetime.datetime.now(datetime.timezone.utc).isoformat()
with open(e/'cache-copy.stdout','wb') as out,open(e/'cache-copy.stderr','wb') as err:p=subprocess.run(['cp','-a','--reflink=auto',str(cache),str(root/'target')],stdout=out,stderr=err)
assert p.returncode==0
source_binary=cache/'release/contextstore-server';copied=root/'target/release/contextstore-server'
assert source_binary.stat().st_ino!=copied.stat().st_ino
r={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_matches_local':checks,'source_archive_sha256':freeze['source_archive_sha256'],'independent_target':str(root/'target'),'cache_source':str(cache),'cache_copy_started':start,'cache_copy_exit':p.returncode,'cached_server_source_inode':source_binary.stat().st_ino,'cached_server_copied_inode':copied.stat().st_ino,'cached_server_sha256':hashlib.sha256(copied.read_bytes()).hexdigest(),'no_active_cargo_before_copy':True,'free_disk_after':shutil.disk_usage(root).free}
(e/'prepare-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
