import pathlib,time,json,socket,datetime
root=pathlib.Path('/sys/class/infiniband/mlx5_1/ports/1/counters')
def read():return {name:int((root/name).read_text()) for name in ['port_xmit_data','port_rcv_data','port_xmit_packets','port_rcv_packets']}
a=read();t=time.monotonic();time.sleep(2);b=read();elapsed=time.monotonic()-t
print(json.dumps({'hostname':socket.gethostname(),'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'seconds':elapsed,'before':a,'after':b,'data_bytes_per_second':{k:(b[k]-a[k])*4/elapsed for k in ['port_xmit_data','port_rcv_data']}},indent=2))
