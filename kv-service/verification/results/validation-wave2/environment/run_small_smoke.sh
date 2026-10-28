set -uo pipefail
root=/home/railtest/cswave1
receipt=$root/wave2/small/evidence
source "$root/wave2/small/client.env"
name=single_real_rail_restores_verified_object
exe=$root/bin/rail_read_e2e
sha256sum "$exe" "$root/wave2/small/client.env" > "$receipt/smoke-hashes.txt"
date -u +%FT%TZ > "$receipt/smoke.started"
timeout 60s "$exe" --ignored --exact --nocapture --test-threads=1 "$name" > "$receipt/smoke.log" 2>&1
code=$?
printf '%s\n' "$code" > "$receipt/smoke.exit_code"
date -u +%FT%TZ > "$receipt/smoke.finished"
cat "$receipt/smoke.log"
exit "$code"
