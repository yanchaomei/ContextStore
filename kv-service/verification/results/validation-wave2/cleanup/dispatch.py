import pathlib,json,subprocess,sys
root=pathlib.Path(__file__).resolve().parent;target=sys.argv[1];phase=sys.argv[2];cfg=json.loads((root/f'{target}-config.json').read_text());script='import json\nCONFIG=json.loads('+repr(json.dumps(cfg))+')\n'+(root/'fixture_cleanup.py').read_text()
if target.startswith('skv'):command=['ssh','-oBatchMode=yes','SKV_'+target[-1],'python3 - '+phase]
else:command=['ssh','-oBatchMode=yes','SKV_2','bash /home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/vm-rxe/guest_ssh.sh '+target[6:]+' python3 - '+phase]
with (root/f'{target}-{phase}.json').open('wb') as out,(root/f'{target}-{phase}.stderr').open('wb') as err:p=subprocess.run(command,input=script.encode(),stdout=out,stderr=err)
print(target,phase,p.returncode);assert p.returncode==0,(target,phase)
