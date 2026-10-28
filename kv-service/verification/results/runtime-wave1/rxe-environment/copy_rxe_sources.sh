#!/usr/bin/env bash
# Run on SKV_1. Only public frozen image and installed QEMU/firmware tools.
set -euo pipefail
dest=skv-node2:/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
rsync -a /home/kvgroup/chaomei/contextstore_multirail_20261002/vm/jammy-server-cloudimg-amd64.img "$dest/vm-rxe/base.img"
rsync -a /usr/bin/qemu-img "$dest/tools/qemu/bin/"
rsync -a /usr/share/OVMF/OVMF_CODE.fd /usr/share/OVMF/OVMF_VARS.fd "$dest/tools/qemu/firmware/"
{
    date -u +%FT%TZ
    hostname
    sha256sum /home/kvgroup/chaomei/contextstore_multirail_20261002/vm/jammy-server-cloudimg-amd64.img /usr/bin/qemu-img /usr/share/OVMF/OVMF_CODE.fd /usr/share/OVMF/OVMF_VARS.fd
} | ssh -oBatchMode=yes skv-node2 'cat > /home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/vm-rxe/evidence/source_hashes.txt'
