from pathlib import Path
import ctypes as ct, hashlib, json, os, time
from rdma_client import RdmaClient
os.environ['CONTEXTSTORE_RDMA_LIB']=str(Path('candidate.so').resolve())
size=64*1024*1024
expected=bytes(range(251))*(size//251)+bytes(range(size%251))
target=ct.create_string_buffer(expected,size)
with RdmaClient('10.31.0.2:55153',device='rxe_c0',gid_index=1,buf_size_mb=1) as client:
 rid=client.register_external_buffer(ct.addressof(target),size)
 client.put(rid,'3:ffirailfuse-demo-20261016',0,size)
 ct.memset(ct.addressof(target),0xA5,size)
 assert client.get_into(rid,'3:ffirailfuse-demo-20261016')==size
 assert target.raw==expected
 client.unregister_external_buffer(rid)
print(json.dumps({'bytes':size,'namespace':'ffi','object_key':'railfuse-demo-20261016','payload_sha256':hashlib.sha256(expected).hexdigest(),'real_ffi_put_get_full_content':True},indent=2))
