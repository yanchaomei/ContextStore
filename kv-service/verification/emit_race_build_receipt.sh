#!/usr/bin/env bash
set -euo pipefail

# Run from the ContextStore repository root on the Linux build host. The
# receipt binds actual source bytes before/after a release test build to the
# exact resulting binary. A separate audit compares those source hashes with
# the named Git commit; a caller label alone is not source attribution.
source_commit=${1:?usage: emit_race_build_receipt.sh SOURCE_COMMIT OUTPUT_DIR}
output_dir=${2:?usage: emit_race_build_receipt.sh SOURCE_COMMIT OUTPUT_DIR}
mkdir -p "$output_dir"
mapfile -t sources < <(
  find kv-service/client-rs/src kv-service/client-rs/tests kv-service/proto \
    kv-service/vendor/rdma-sys/src -type f \( -name '*.rs' -o -name '*.proto' \) -print |
    LC_ALL=C sort
)
sources+=(
  Cargo.toml
  Cargo.lock
  kv-service/client-rs/Cargo.toml
  kv-service/client-rs/build.rs
  kv-service/vendor/rdma-sys/Cargo.toml
)
printf '%s\n' "$source_commit" > "$output_dir/source-commit-label.txt"
rustc --version --verbose > "$output_dir/rustc-version.txt"
cargo --version > "$output_dir/cargo-version.txt"
sha256sum "${sources[@]}" > "$output_dir/source-before.sha256"
cargo test -p contextstore-client-rs --features rdma --release --test rail_read_e2e --no-run \
  > "$output_dir/build.stdout" 2> "$output_dir/build.stderr"
sha256sum "${sources[@]}" > "$output_dir/source-after.sha256"
cmp "$output_dir/source-before.sha256" "$output_dir/source-after.sha256"
sha256sum target/release/deps/rail_read_e2e-999328bb0f338845 \
  > "$output_dir/binary.sha256"
(
  cd "$output_dir"
  sha256sum source-before.sha256 source-after.sha256 binary.sha256 > receipt.sha256
)
