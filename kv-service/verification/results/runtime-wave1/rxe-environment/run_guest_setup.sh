#!/usr/bin/env bash
set -uo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/vm-rxe
role="$1"
bash "$root/guest_ssh.sh" "$role" 'mkdir -p /home/railtest/cswave1; cat > /home/railtest/cswave1/setup_guest_rxe.sh' < "$root/setup_guest_rxe.sh"
bash "$root/guest_ssh.sh" "$role" 'bash /home/railtest/cswave1/setup_guest_rxe.sh'
status=$?
printf '%s\n' "$status" > "$root/evidence/${role}_setup.exit_code"
