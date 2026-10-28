set -euo pipefail
root=/home/kvgroup/chaomei/contextstore_validation_wave2_20261029
source "$root/client.env"
cd "$root"
exe="$root/bin/rail_read_e2e-d6145e305ed0b23e"
printf '%s  %s\n' '06a2a6657c14b1099dead47a8dcb120efaa9676c880f09a302c483a813a0ee26' "$exe" | sha256sum -c -
sha256sum "$exe" "$root/client.env" > "$root/evidence/baseline-hashes.txt"
ldd "$exe" > "$root/evidence/baseline-ldd.txt"
if grep -q 'not found' "$root/evidence/baseline-ldd.txt"; then exit 1; fi
date -u +%FT%TZ > "$root/evidence/baseline.started"
set +e
timeout 120s "$exe" --ignored --exact --nocapture --test-threads=1 single_real_rail_restores_verified_object > "$root/evidence/baseline.log" 2>&1
code=$?
set -e
printf '%s\n' "$code" > "$root/evidence/baseline.exit_code"
date -u +%FT%TZ > "$root/evidence/baseline.finished"
cat "$root/evidence/baseline.log"
exit "$code"
