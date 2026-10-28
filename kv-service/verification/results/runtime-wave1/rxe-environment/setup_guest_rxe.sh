#!/usr/bin/env bash
set -euo pipefail
root=/home/railtest/cswave1
mkdir -p "$root/evidence" "$root/bin" "$root/data/stripe0" "$root/data/stripe1" "$root/redis"
exec > "$root/evidence/setup.log" 2>&1
date -u +%FT%TZ
uname -a
cloud-init status
sudo apt-get update -o Acquire::Retries=1 -o Acquire::http::Timeout=30
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    rdma-core ibverbs-providers ibverbs-utils iproute2 redis-server
if ! modinfo rdma_rxe >/dev/null 2>&1; then
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "linux-modules-extra-$(uname -r)"
fi
sudo modprobe rdma_rxe
sudo rdma link add rxe0 type rxe netdev rail0
sudo rdma link add rxe1 type rxe netdev rail1
printf '%s\n' 'railtest soft memlock unlimited' 'railtest hard memlock unlimited' | sudo tee /etc/security/limits.d/90-cswave1.conf
{
    date -u +%FT%TZ
    hostname
    uname -a
    ip -br addr
    rdma link show
    ibv_devices
    for dev in rxe0 rxe1; do
        ibv_devinfo -d "$dev"
        for gid in 0 1 2 3; do
            if [ -f "/sys/class/infiniband/$dev/ports/1/gids/$gid" ]; then
                printf '%s gid%s ' "$dev" "$gid"
                cat "/sys/class/infiniband/$dev/ports/1/gids/$gid"
                cat "/sys/class/infiniband/$dev/ports/1/gid_attrs/types/$gid" 2>/dev/null || true
            fi
        done
    done
    dpkg-query -W rdma-core ibverbs-providers ibverbs-utils iproute2 redis-server
} > "$root/evidence/topology.txt" 2>&1
date -u +%FT%TZ > "$root/evidence/setup.finished"
