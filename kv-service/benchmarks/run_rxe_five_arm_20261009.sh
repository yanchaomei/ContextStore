#!/usr/bin/env bash
set -euo pipefail

# Execute inside the isolated RXE client guest after both published object keys
# have been seeded. The two sizes are sequential to avoid CPU/disk interference.
binary=${1:?usage: run_rxe_five_arm_20261009.sh BINARY OUTPUT_ROOT}
output_root=${2:?usage: run_rxe_five_arm_20261009.sh BINARY OUTPUT_ROOT}
collector=${COLLECTOR:-/home/railtest/collect_rail_modes.py}
mkdir -p "$output_root"

for entry in '64mib:paper640/__combined__' '256mib:paper256direct0/__combined__'; do
  size=${entry%%:*}
  key=${entry#*:}
  python3 "$collector" \
    --binary "$binary" \
    --environment soft-roce \
    --coordinator http://10.31.0.2:55151 \
    --namespace rust-bench \
    --object-key "$key" \
    --rail r0,rxe_c0,10.31.0.2:55153,10.31.0.2:55153,1,1 \
    --rail r1,rxe_c1,10.31.0.2:55153,10.32.0.2:55154,1,1 \
    --arm-set five \
    --blocks 10 \
    --iterations 5 \
    --warmup 1 \
    --output-dir "$output_root/$size"
done
