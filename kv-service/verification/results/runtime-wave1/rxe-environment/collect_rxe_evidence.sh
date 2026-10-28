#!/usr/bin/env bash
set -euo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/vm-rxe
pgrep -u kvgroup -af '^/usr/bin/qemu-system-x86_64 -name csw1-rxe-' > "$root/evidence/active_qemu_processes.txt"
ss -lun '( sport = :24351 or sport = :24352 or sport = :24361 or sport = :24362 )' > "$root/evidence/active_udp_sockets.txt"
sha256sum "$root/base.img" > "$root/evidence/base_hash_after_run.txt"
for role in server client; do
    bash "$root/guest_ssh.sh" "$role" 'python3 -' < "$root/guest_snapshot.py" > "$root/evidence/${role}_active_guest.json"
    bash "$root/guest_ssh.sh" "$role" 'tar -C /home/railtest/cswave1 -czf - evidence' > "$root/evidence/${role}_guest_evidence.tar.gz"
    cp "$root/$role/command.json" "$root/evidence/${role}_command_udp.json"
    cp "$root/$role/command.json.tcp-attempt" "$root/evidence/${role}_command_tcp.json"
    cp "$root/$role/serial.log.tcp-attempt" "$root/evidence/${role}_serial_tcp.log"
    cp "$root/$role/qemu.log.tcp-attempt" "$root/evidence/${role}_qemu_tcp.log"
    cp "$root/$role/serial.log" "$root/evidence/${role}_serial_udp.log"
    cp "$root/$role/qemu.log" "$root/evidence/${role}_qemu_udp.log"
done
