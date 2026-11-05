#!/usr/bin/env bash
# Runs a command in every Rust service. It changes directory, because rustup picks
# the toolchain from rust-toolchain.toml by the working directory.
set -euo pipefail

for manifest in services/*/Cargo.toml infra/benchmark/*/Cargo.toml; do
  (cd "$(dirname "$manifest")" && "$@")
done
