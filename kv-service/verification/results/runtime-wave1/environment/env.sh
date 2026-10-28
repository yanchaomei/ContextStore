#!/usr/bin/env bash
# Source only in the dedicated build subprocess.
export WAVE1_ROOT=/home/kvgroup/chaomei/contextstore_runtime_wave1_20261029
export CARGO_HOME="$WAVE1_ROOT/tools/cargo"
export PATH="$WAVE1_ROOT/tools/rust/bin:$PATH"
export CARGO_TARGET_DIR="$WAVE1_ROOT/target"
export CARGO_BUILD_JOBS=4
export LIBCLANG_PATH=/usr/lib/x86_64-linux-gnu
export PROTOC=/usr/local/bin/protoc
export TMPDIR="$WAVE1_ROOT/tmp"

