#!/usr/bin/env bash
set -uo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
attempt="$1"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
python3 "$root/final-verification/scripts/run_final_native_ffi.py" --host-attempt "$attempt" \
    > "$attempt/native-launch-$stamp.stdout" \
    2> "$attempt/native-launch-$stamp.stderr"
status=$?
printf '%s\n' "$status" > "$attempt/native-launch-$stamp.exit_code"
exit "$status"
