#!/usr/bin/env bash
# Read-only feasibility probe. Do not load drivers, start VMs, or install packages.
set -u
date -u +%Y-%m-%dT%H:%M:%SZ
hostname
uname -a
id
df -h /home/kvgroup/chaomei
free -h
for cmd in qemu-system-x86_64 qemu-img cloud-localds genisoimage xorriso rdma modinfo virsh; do
    command -v "$cmd" || true
done
qemu-system-x86_64 --version
qemu-system-x86_64 -accel help
ls -l /dev/kvm
test -r /dev/kvm && test -w /dev/kvm && echo KVM_USER_ACCESSIBLE=true || echo KVM_USER_ACCESSIBLE=false
test -r /usr/share/OVMF/OVMF_CODE.fd && echo OVMF_CODE_PRESENT=true || true
test -r /usr/share/qemu/firmware/60-edk2-x86_64.json && echo EDK2_CONFIG_PRESENT=true || true
ldconfig -p | grep -E 'libaio.so.1|libgnutls.so.30|libnettle.so.7|libffi.so.7' || true
if command -v rdma >/dev/null; then rdma link show; fi
if command -v modinfo >/dev/null; then modinfo rdma_rxe 2>&1 | head -12; fi
if command -v ss >/dev/null; then ss -ltn '( sport = :22351 or sport = :22352 or sport = :24351 or sport = :24352 )'; fi
