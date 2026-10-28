#!/usr/bin/env bash
set -euo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
source "$root/wave2/provider-client.env"
frozen="$root/wave2/soak-frozen-bin-v2"
sha256sum -c "$root/wave2/soak-build/frozen-v2.sha256" > "$root/wave2/soak-build/prelaunch-hashes.log"
set +e
"$frozen/rail_runtime_soak" negative_control_corrupt_payload_must_fail --ignored --exact --nocapture > "$root/wave2/soak-build/oracle-negative-v2.log" 2>&1
negative_status=$?
set -e
printf '%s\n' "$negative_status" > "$root/wave2/soak-build/oracle-negative-v2.exit"
test "$negative_status" -eq 101
"$frozen/rail_runtime_soak" oracle_rejects_corrupt_and_truncated_payloads --exact --nocapture > "$root/wave2/soak-build/oracle-positive-v2.log" 2>&1
printf '0\n' > "$root/wave2/soak-build/oracle-positive-v2.exit"
python3 "$frozen/run_runtime_soak.py" --binary "$frozen/rail_runtime_soak" --output "$root/wave2/soak-smoke-v2-60s" --seconds 60
sha256sum -c "$root/wave2/soak-build/frozen-v2.sha256" > "$root/wave2/soak-build/long-prelaunch-hashes.log"
python3 "$frozen/run_runtime_soak.py" --binary "$frozen/rail_runtime_soak" --output "$root/wave2/soak-long-v2-7200s" --seconds 7200
sha256sum -c "$root/wave2/soak-build/frozen-v2.sha256" > "$root/wave2/soak-build/postrun-hashes.log"
