#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
: "${SMLT_ZIG_ROOT:?Set SMLT_ZIG_ROOT to official Zig 0.12.1 directory}"
export RUSTFLAGS="${RUSTFLAGS:-} --remap-path-prefix=$PWD=."
rustup target add x86_64-unknown-linux-musl x86_64-pc-windows-gnu
cargo build --release --locked --target x86_64-unknown-linux-musl
CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER="$PWD/scripts/windows-cross-link.py" \
    cargo build --release --locked --target x86_64-pc-windows-gnu
