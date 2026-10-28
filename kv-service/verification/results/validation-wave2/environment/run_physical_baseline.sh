set -uo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
receipt=$root/wave2/environment/physical-baseline
mkdir -p "$receipt"
source "$root/provider-client.env"
cp "$root/provider-client.env" "$root/wave2/provider-client.env"
cd "$root/src-baseline" || exit 1
sha256sum -c "$root/source_sha256.txt" > "$receipt/source-verification.log" || exit 1
for pair in 'rdma_e2e-8ac8901d52660419 rdma_put_and_get_round_trip' 'rail_read_e2e-d6145e305ed0b23e single_real_rail_restores_verified_object' 'rail_read_e2e-d6145e305ed0b23e same_object_matches_single_and_dual_rail'; do
 read -r binary name <<< "$pair"
 exe="$root/target-provider-baseline/debug/deps/$binary"
 sha256sum "$exe" > "$receipt/$name.binary.sha256"
 date -u +%FT%TZ > "$receipt/$name.started"
 printf '%s\n' "timeout 120s $exe --ignored --exact --nocapture --test-threads=1 $name" > "$receipt/$name.command"
 timeout 120s "$exe" --ignored --exact --nocapture --test-threads=1 "$name" > "$receipt/$name.log" 2>&1
 code=$?
 printf '%s\n' "$code" > "$receipt/$name.exit_code"
 date -u +%FT%TZ > "$receipt/$name.finished"
 if [ "$code" != 0 ]; then cat "$receipt/$name.log"; exit "$code"; fi
 tail -5 "$receipt/$name.log"
done
