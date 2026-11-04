#!/usr/bin/env bash
# Runs a command through uv in every Python service. A new service needs no hook changes.
set -euo pipefail

for project in services/*/pyproject.toml; do
  uv run --frozen --directory "$(dirname "$project")" "$@"
done
