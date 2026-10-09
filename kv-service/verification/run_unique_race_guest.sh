#!/usr/bin/env bash
set -euo pipefail

# Test-only invocation inside the isolated RXE client guest. The launch
# manifest hashes the exact test binary before execution, rather than binding
# a different file by its hash after the experiment.
binary=${1:?usage: run_unique_race_guest.sh BINARY OUTPUT_PREFIX OBJECT_MIB SOURCE_COMMIT}
output_prefix=${2:?usage: run_unique_race_guest.sh BINARY OUTPUT_PREFIX OBJECT_MIB SOURCE_COMMIT}
object_mib=${3:?usage: run_unique_race_guest.sh BINARY OUTPUT_PREFIX OBJECT_MIB SOURCE_COMMIT}
source_commit=${4:?usage: run_unique_race_guest.sh BINARY OUTPUT_PREFIX OBJECT_MIB SOURCE_COMMIT}
mkdir -p "$(dirname "$output_prefix")"
{
  printf 'source_commit=%s\nobject_mib=%s\n' "$source_commit" "$object_mib"
  sha256sum "$binary"
} > "$output_prefix.launch"

set +e
CS_RAIL_TIMING=1 \
CS_RAIL_COORDINATOR=http://10.31.0.2:55151 \
CS_RAIL_LISTENER0=10.31.0.2:55153 \
CS_RAIL_LISTENER1=10.32.0.2:55154 \
CS_RAIL_DEVICE0=rxe_c0 \
CS_RAIL_DEVICE1=rxe_c1 \
CS_RAIL_GID0=1 \
CS_RAIL_GID1=1 \
CS_RAIL_OBJECT_MIB="$object_mib" \
"$binary" --ignored --exact unique_generations_survive_concurrent_direct_rail_reads --nocapture \
  > "$output_prefix.log" 2>&1
test_exit_code=$?
set -e
printf '%s\n' "$test_exit_code" > "$output_prefix.exit"
exit "$test_exit_code"
