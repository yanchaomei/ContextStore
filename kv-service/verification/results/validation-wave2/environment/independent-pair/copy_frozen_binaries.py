import subprocess,pathlib,json,datetime
local=pathlib.Path(__file__).resolve().parent
root='/home/kvgroup/chaomei/contextstore_validation_wave2_20261029'
def ssh(host,command):return ['ssh','-oBatchMode=yes','-oConnectTimeout=10',host,command]
def target(n,command):return ssh('SKV_5',command) if n==5 else ssh('SKV_1','ssh -oBatchMode=yes skv-node6 '+"'"+command.replace("'","'\\''")+"'")
prepare="import pathlib,shutil; p=pathlib.Path('"+root+"'); assert not p.exists(); assert shutil.disk_usage(p.parent).free>1073741824; p.mkdir(); [(p/x).mkdir() for x in ['bin','lib','evidence']]"
for n in [5,6]:subprocess.run(target(n,'python3 -'),input=prepare,text=True,check=True)
transfers=[
 ('server',ssh('SKV_4','tar -chf - -C /home/kvgroup/chaomei/contextstore_runtime_wave1_provider_20261029 bin/contextstore-server'),target(5,'tar -xf - -C '+root)),
 ('redis',ssh('SKV_4','tar -chf - -C /usr/bin redis-server redis-cli'),target(5,'tar -xf - -C '+root+'/bin')),
 ('redis_private_libs',ssh('SKV_4','tar -chf - -C /lib/x86_64-linux-gnu liblua5.1-cjson.so.0 liblua5.1-bitop.so.0 liblua5.1.so.0 libjemalloc.so.2 libhiredis.so.0.14'),target(5,'tar -xf - -C '+root+'/lib')),
 ('baseline_clients',ssh('SKV_2','tar -chf - -C /home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/target-provider-baseline/debug/deps rail_read_e2e-d6145e305ed0b23e rdma_e2e-8ac8901d52660419'),target(6,'tar -xf - -C '+root+'/bin'))]
receipt=[]
for name,source,dest in transfers:
 print('transfer',name,flush=True)
 with open(local/(name+'.stderr'),'wb') as err:
  p=subprocess.Popen(source,stdout=subprocess.PIPE,stderr=err)
  q=subprocess.Popen(dest,stdin=p.stdout,stdout=subprocess.PIPE,stderr=err)
  p.stdout.close();out=q.communicate()[0];code=p.wait()
  assert code==0 and q.returncode==0,(name,code,q.returncode)
 receipt.append({'name':name,'source':source,'dest':dest,'source_exit':code,'dest_exit':q.returncode})
(local/'copy-receipt.json').write_text(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'transfers':receipt},indent=2)+'\n')
