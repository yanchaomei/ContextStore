#!/usr/bin/env bash
set -euo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
vm="$root/vm-rxe"
target="$root/target-provider-baseline/debug"
scp_common=(-oBatchMode=yes -oStrictHostKeyChecking=yes -oUserKnownHostsFile="$vm/known_hosts" -i "$vm/railtest_ed25519")
sha256sum "$target/contextstore-server" "$target/deps/rdma_e2e-8ac8901d52660419" "$target/deps/rail_read_e2e-d6145e305ed0b23e" > "$vm/evidence/deployed_source_binaries.sha256"
scp "${scp_common[@]}" -P 22351 "$target/contextstore-server" railtest@127.0.0.1:/home/railtest/cswave1/bin/contextstore-server
scp "${scp_common[@]}" -P 22351 "$vm/server.toml" "$vm/guest_server_run.sh" railtest@127.0.0.1:/home/railtest/cswave1/
scp "${scp_common[@]}" -P 22352 "$target/deps/rdma_e2e-8ac8901d52660419" railtest@127.0.0.1:/home/railtest/cswave1/bin/rdma_e2e
scp "${scp_common[@]}" -P 22352 "$target/deps/rail_read_e2e-d6145e305ed0b23e" railtest@127.0.0.1:/home/railtest/cswave1/bin/rail_read_e2e
scp "${scp_common[@]}" -P 22352 "$vm/client.env" "$vm/guest_run_baseline.sh" railtest@127.0.0.1:/home/railtest/cswave1/
for role in server client; do
    bash "$vm/guest_ssh.sh" "$role" 'sha256sum /home/railtest/cswave1/bin/*; for bin in /home/railtest/cswave1/bin/*; do ldd "$bin"; done' > "$vm/evidence/${role}_deployed_binaries.txt"
done
