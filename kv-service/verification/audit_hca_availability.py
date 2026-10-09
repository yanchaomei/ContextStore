"""Audit read-only Verbs device and link-state receipts from SKV nodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


LINK = re.compile(
    r"^link (mlx5_[01])/1 state (\S+) physical_state (\S+) netdev (\S+)\s*$",
    re.M,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    nodes = []
    for index in range(1, 8):
        path = args.receipts / f"skv-node{index}.txt"
        raw = path.read_text()
        links = LINK.findall(raw)
        if "2 HCAs found:" not in raw or len(links) != 2:
            raise ValueError(f"missing Verbs device or link in {path}")
        by_device = {device: (state, physical, netdev) for device, state, physical, netdev in links}
        if set(by_device) != {"mlx5_0", "mlx5_1"}:
            raise ValueError(f"unexpected device set in {path}")
        if by_device["mlx5_0"][:2] != ("DOWN", "DISABLED") or by_device["mlx5_1"][:2] != (
            "ACTIVE", "LINK_UP"
        ):
            raise ValueError(f"two-path availability changed on node {index}")
        nodes.append(
            {
                "node": f"skv-node{index}",
                "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "verbs_devices_enumerated": 2,
                "usable_active_ports": 1,
                "links": {
                    device: {
                        "state": state,
                        "physical_state": physical,
                        "netdev": netdev,
                    }
                    for device, (state, physical, netdev) in by_device.items()
                },
            }
        )
    result = {
        "collection_date": "2026-10-09",
        "scope": "read-only ibv_devinfo -l and rdma link show on SKV nodes 1-7; nodes 6-7 reached via SKV_1 jump",
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "nodes": nodes,
        "conclusion": "each observed node has two enumerated Verbs device names but only mlx5_1/1 is ACTIVE/LINK_UP; a single Worker lacks two simultaneously usable physical local Rails in this cluster snapshot",
        "non_claim": "does not test cabling changes, HCA performance, PCIe/NUMA bandwidth, or future availability",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output, "7 nodes, 1 active physical port each")


if __name__ == "__main__":
    main()
