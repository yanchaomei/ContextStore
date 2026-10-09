#!/usr/bin/env bash
set -euo pipefail

# Test-only one-Rail GET against the isolated server fail-stop launcher.
binary=${1:?usage: run_server_fault_trigger_guest.sh BINARY OUTPUT_PREFIX OBJECT_KEY}
output_prefix=${2:?usage: run_server_fault_trigger_guest.sh BINARY OUTPUT_PREFIX OBJECT_KEY}
object_key=${3:?usage: run_server_fault_trigger_guest.sh BINARY OUTPUT_PREFIX OBJECT_KEY}
mkdir -p "$(dirname "$output_prefix")"
{
  printf 'namespace=rail-e2e\nobject_key=%s\n' "$object_key"
  sha256sum "$binary"
} > "$output_prefix.launch"
set +e
"$binary" \
  --environment soft-roce \
  --coordinator http://10.31.0.2:55151 \
  --namespace rail-e2e \
  --object-key "$object_key" \
  --warmup 1 --iterations 1 \
  --rail r0,rxe_c0,10.31.0.2:55153,10.31.0.2:55153,1,1 \
  > "$output_prefix.log" 2>&1
trigger_exit_code=$?
set -e
printf '%s\n' "$trigger_exit_code" > "$output_prefix.exit"
exit "$trigger_exit_code"
