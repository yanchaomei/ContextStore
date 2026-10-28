#!/usr/bin/env python3
import pathlib, json, os, sys
role = sys.argv[1]
assert role in ['server', 'client']
path=pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/vm-rxe')/role
command=json.loads((path/'command.json').read_text())
log=os.open(path/'qemu.log', os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
os.dup2(log,1); os.dup2(log,2); os.close(log)
os.execvp(command[0],command)
