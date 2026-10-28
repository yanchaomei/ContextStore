#!/usr/bin/env bash
set -uo pipefail
root=/home/railtest/cswave1
source "$root/wave2/client.env"
receipt="$root/wave2/evidence/baseline"
mkdir -p "$receipt"
date -u +%FT%TZ > "$receipt/baseline.started"
for pair in 'rdma_e2e rdma_put_and_get_round_trip' 'rail_read_e2e single_real_rail_restores_verified_object' 'rail_read_e2e same_object_matches_single_and_dual_rail'; do
    read -r binary name <<< "$pair"
    printf '%s\n' "timeout 120s $root/bin/$binary --ignored --exact --nocapture --test-threads=1 $name" > "$receipt/$name.command"
    date -u +%FT%TZ > "$receipt/$name.started"
    timeout 120s "$root/bin/$binary" --ignored --exact --nocapture --test-threads=1 "$name" > "$receipt/$name.log" 2>&1
    status=$?
    printf '%s\n' "$status" > "$receipt/$name.exit_code"
    date -u +%FT%TZ > "$receipt/$name.finished"
done
date -u +%FT%TZ > "$receipt/baseline.finished"
