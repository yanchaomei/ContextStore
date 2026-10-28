import hashlib,json,pathlib,subprocess
P=pathlib.Path(__file__).resolve().parent;W=P.parents[2]/'outputs/implementation-wave1-20261029/reviews'
payload={'script':(W/'replay_twox_hash.py').read_text(),'source':(W/'twox-hash-1.6.3/src/xxh3.rs').read_text()}
code='''import pathlib,json,hashlib,sys
p=json.load(sys.stdin)
original=pathlib.Path.read_text
pathlib.Path.read_text=lambda path,*a,**kw: p['source'] if str(path).endswith('/twox-hash-1.6.3/src/xxh3.rs') else original(path,*a,**kw)
original_bytes=pathlib.Path.read_bytes
pathlib.Path.read_bytes=lambda path: p['source'].encode() if str(path).endswith('/twox-hash-1.6.3/src/xxh3.rs') else original_bytes(path)
ns={'__name__':'frozen_replay','__file__':'/home/kvgroup/chaomei/contextstore_validation_wave2_hash_replay_v1/replay_twox_hash.py'}
exec(p['script'],ns)
receipt=ns['compute_receipt']()
for s in [1,8]:
 n=s*1048576;data=(bytes(range(251))*(n//251+1))[:n];receipt['deterministic_pattern_i_mod_251_hashes'][s]=ns['h'](data)
receipt['replay_script_sha256']=hashlib.sha256(p['script'].encode()).hexdigest()
receipt['host']='SKV1 in-memory independent hash computation, no filesystem write, not performance hosts'
print(json.dumps(receipt,indent=2))
'''
import shlex
r=subprocess.run(['ssh','SKV_1','python3 -c '+shlex.quote(code)],input=json.dumps(payload).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120);(P/'independent-hash-replay.stderr').write_bytes(r.stderr);assert r.returncode==0,r.stderr
receipt=json.loads(r.stdout);(P/'independent-hash-replay.json').write_text(json.dumps(receipt,indent=2)+'\n')
known=json.loads((P/'known-pattern-hashes.json').read_text());assert all(receipt['deterministic_pattern_i_mod_251_hashes'][k]==v for k,v in known.items())
print(json.dumps({'state':'PASS','known_hashes':known,'replay_script_sha256':receipt['replay_script_sha256']},indent=2))
