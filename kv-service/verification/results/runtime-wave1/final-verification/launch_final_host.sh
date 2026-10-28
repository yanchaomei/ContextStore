#!/usr/bin/env bash
# Run only after explicit parent GO after the performance pilot.
set -uo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
script_dir="$root/final-verification/scripts"
label="$1"
changed_files="$2"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$root/final-verification"
python3 "$script_dir/run_final_host.py" --source-label "$label" --changed-files "$changed_files" \
    > "$root/final-verification/launch-$stamp.stdout" \
    2> "$root/final-verification/launch-$stamp.stderr"
status=$?
printf '%s\n' "$status" > "$root/final-verification/launch-$stamp.exit_code"
exit "$status"
