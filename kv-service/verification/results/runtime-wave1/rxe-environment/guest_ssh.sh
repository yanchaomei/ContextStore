#!/usr/bin/env bash
set -euo pipefail
root=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/vm-rxe
role="$1"
shift
case "$role" in
    server) port=22351 ;;
    client) port=22352 ;;
    *) exit 2 ;;
esac
exec ssh -oBatchMode=yes -oConnectTimeout=5 -oStrictHostKeyChecking=accept-new \
    -oUserKnownHostsFile="$root/known_hosts" -i "$root/railtest_ed25519" \
    -p "$port" railtest@127.0.0.1 "$@"
