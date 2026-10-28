#!/usr/bin/env python3
import pathlib,json,shutil
root=pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/vm-rxe')
for role in ['server','client']:
    path=root/role
    for name in ['command.json','serial.log','qemu.log']:
        shutil.copy2(path/name,path/(name+'.tcp-attempt'))
    command=json.loads((path/'command.json').read_text())
    for index,arg in enumerate(command):
        if arg.startswith('socket,id=rail0,'):
            command[index]='socket,id=rail0,udp=127.0.0.1:24361,localaddr=127.0.0.1:24351' if role=='server' else 'socket,id=rail0,udp=127.0.0.1:24351,localaddr=127.0.0.1:24361'
        elif arg.startswith('socket,id=rail1,'):
            command[index]='socket,id=rail1,udp=127.0.0.1:24362,localaddr=127.0.0.1:24352' if role=='server' else 'socket,id=rail1,udp=127.0.0.1:24352,localaddr=127.0.0.1:24362'
    (path/'command.json').write_text(json.dumps(command,indent=2)+'\n')
print('Changed only the task-owned QEMU data backend from TCP to UDP sockets')
