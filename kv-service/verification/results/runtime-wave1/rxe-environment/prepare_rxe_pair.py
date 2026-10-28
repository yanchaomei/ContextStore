#!/usr/bin/env python3
import pathlib, subprocess, uuid, json, shutil

root = pathlib.Path('/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029')
vm = root / 'vm-rxe'
qemu_img = root / 'tools/qemu/bin/qemu-img'
key = vm / 'railtest_ed25519'
if not key.exists():
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'cs-wave1-rxe-20261029', '-f', str(key)], check=True)
key.chmod(0o600)
public = key.with_suffix('.pub').read_text().strip()
manifest = {'private_key_location_only': str(key), 'guests': {}}
for role, suffix, management_port in [('server', 1, 22351), ('client', 2, 22352)]:
    path = vm / role
    path.mkdir()
    guest_uuid = str(uuid.uuid4())
    subprocess.run([str(qemu_img), 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(vm/'base.img'), str(path/'disk.qcow2'), '16G'], check=True)
    shutil.copy2(root/'tools/qemu/firmware/OVMF_VARS.fd', path/'OVMF_VARS.fd')
    (path/'meta-data').write_text(f'instance-id: {guest_uuid}\nlocal-hostname: csw1-rxe-{role}\n')
    (path/'user-data').write_text(f'''#cloud-config
hostname: csw1-rxe-{role}
manage_etc_hosts: true
ssh_pwauth: false
users:
  - name: railtest
    groups: [sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: true
    ssh_authorized_keys:
      - {public}
package_update: false
''')
    (path/'network-config').write_text(f'''version: 2
ethernets:
  management:
    match:
      macaddress: "52:54:00:29:50:0{suffix}"
    set-name: mgmt0
    dhcp4: true
  path0:
    match:
      macaddress: "52:54:00:29:51:0{suffix}"
    set-name: rail0
    addresses: [10.81.1.{suffix}/24]
  path1:
    match:
      macaddress: "52:54:00:29:52:0{suffix}"
    set-name: rail1
    addresses: [10.82.1.{suffix}/24]
''')
    subprocess.run(['genisoimage', '-quiet', '-output', str(path/'seed.iso'), '-volid', 'cidata', '-joliet', '-rock', 'user-data', 'meta-data', 'network-config'], cwd=path, check=True)
    data0 = f'listen=127.0.0.1:24351' if role == 'server' else f'connect=127.0.0.1:24351'
    data1 = f'listen=127.0.0.1:24352' if role == 'server' else f'connect=127.0.0.1:24352'
    command = ['sudo', '-n', '/usr/bin/qemu-system-x86_64', '-name', f'csw1-rxe-{role}-20261029', '-uuid', guest_uuid,
        '-enable-kvm', '-runas', 'kvgroup', '-cpu', 'host', '-smp', '2', '-m', '3072', '-machine', 'q35',
        '-drive', f'if=pflash,format=raw,readonly=on,file={root}/tools/qemu/firmware/OVMF_CODE.fd',
        '-drive', f'if=pflash,format=raw,file={path}/OVMF_VARS.fd',
        '-drive', f'file={path}/disk.qcow2,if=virtio,format=qcow2',
        '-drive', f'file={path}/seed.iso,media=cdrom,if=ide',
        '-netdev', f'user,id=mgmt,hostfwd=tcp:127.0.0.1:{management_port}-:22', '-device', f'virtio-net-pci,netdev=mgmt,mac=52:54:00:29:50:0{suffix}',
        '-netdev', f'socket,id=rail0,{data0}', '-device', f'virtio-net-pci,netdev=rail0,mac=52:54:00:29:51:0{suffix}',
        '-netdev', f'socket,id=rail1,{data1}', '-device', f'virtio-net-pci,netdev=rail1,mac=52:54:00:29:52:0{suffix}',
        '-display', 'none', '-monitor', 'none', '-serial', f'file:{path}/serial.log', '-pidfile', str(path/'qemu.pid')]
    (path/'command.json').write_text(json.dumps(command, indent=2)+'\n')
    manifest['guests'][role] = {'uuid': guest_uuid, 'management_port': management_port, 'path0': f'10.81.1.{suffix}', 'path1': f'10.82.1.{suffix}', 'directory': str(path)}
(vm/'evidence/topology.json').write_text(json.dumps(manifest, indent=2)+'\n')
print(json.dumps({'guests': manifest['guests'], 'private_key': 'generated, not printed'}, indent=2))
