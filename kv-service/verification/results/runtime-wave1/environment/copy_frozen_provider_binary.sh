#!/usr/bin/env bash
# Run on SKV_1. Streaming relay only; no source files or credentials are copied there.
set -euo pipefail
source_bin=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/target-provider-baseline/debug/contextstore-server
dest_bin=/home/kvgroup/chaomei/contextstore_runtime_wave1_provider_20261029/bin/contextstore-server
source_receipt=$(ssh -n -oBatchMode=yes skv-node2 "sha256sum '$source_bin'")
source_hash=${source_receipt%% *}
ssh -n -oBatchMode=yes skv-node2 "cat '$source_bin'" | ssh -oBatchMode=yes skv-node4 "cat > '$dest_bin' && chmod 700 '$dest_bin'"
dest_receipt=$(ssh -n -oBatchMode=yes skv-node4 "sha256sum '$dest_bin'")
dest_hash=${dest_receipt%% *}
test "$source_hash" = "$dest_hash"
{
    printf 'source_commit=%s\n' 3fe4b26d72fffbe3a3d95236b858ab2ed0d78a6b
    printf 'source=skv-node2:%s\n' "$source_bin"
    printf 'destination=skv-node4:%s\n' "$dest_bin"
    printf 'source_sha256=%s\ndestination_sha256=%s\nverified=true\n' "$source_hash" "$dest_hash"
    date -u +%FT%TZ
} | ssh -oBatchMode=yes skv-node4 'cat > /home/kvgroup/chaomei/contextstore_runtime_wave1_provider_20261029/evidence/server_copy_receipt.txt'
printf 'Verified frozen server binary SHA256 %s\n' "$source_hash"
