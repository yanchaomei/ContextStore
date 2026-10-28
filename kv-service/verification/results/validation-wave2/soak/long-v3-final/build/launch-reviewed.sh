#!/usr/bin/env bash
set -euo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
source "$root/wave2/provider-client.env"
frozen="$root/wave2/soak-frozen-bin-v3"
sha256sum -c "$root/wave2/soak-build-v3/frozen.sha256" > "$root/wave2/soak-build-v3/long-prelaunch-hashes.log"
set +e
python3 "$frozen/run_runtime_soak.py" --binary "$frozen/rail_runtime_soak" --output "$root/wave2/soak-long-v3-7200s" --seconds 7200
campaign_status=$?
sha256sum -c "$root/wave2/soak-build-v3/frozen.sha256" > "$root/wave2/soak-build-v3/long-postrun-hashes.log"
freeze_status=$?
set -e
printf '%s\n' "$campaign_status" > "$root/wave2/soak-build-v3/campaign.exit"
printf '%s\n' "$freeze_status" > "$root/wave2/soak-build-v3/postrun-freeze.exit"
if [ "$campaign_status" -ne 0 ]; then exit "$campaign_status"; fi
exit "$freeze_status"
