#!/usr/bin/env bash
set -euo pipefail
# Execute on SKV_1. Read-only source; all writes are in this task's SKV_2 root.
dest=skv-node2:/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
rsync -a --stats /home/kvgroup/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/ "$dest/tools/rust/"
rsync -a --stats /home/kvgroup/.cargo/registry/ "$dest/tools/cargo/registry/"
# Config was checked for secret fields and authenticated/non-public URLs.
test "$(sha256sum /home/kvgroup/.cargo/config.toml | cut -d' ' -f1)" = b5ddd6eec01fdaa015191c58d1337e193a8cbad5b3dccc0ac0a8ae5b1e2e2e10
rsync -a /home/kvgroup/.cargo/config.toml "$dest/tools/cargo/config.toml"
{
    hostname
    /home/kvgroup/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin/rustc -vV
    /home/kvgroup/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin/cargo -vV
    sha256sum /home/kvgroup/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin/rustc /home/kvgroup/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin/cargo /home/kvgroup/.cargo/config.toml
} | ssh -oBatchMode=yes skv-node2 'cat > /home/kvgroup/chaomei/contextstore_runtime_wave1_20261029/evidence/toolchain_source.txt'
