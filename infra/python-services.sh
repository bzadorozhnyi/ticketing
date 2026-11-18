#!/usr/bin/env bash
# Runs a command through uv in every Python service. A new service needs no hook changes.
# With --only-with <path>, only in the services that have that file (relative to the service).
set -euo pipefail

only_with=""
if [ "${1:-}" = "--only-with" ]; then
  only_with=$2
  shift 2
fi

for project in services/*/pyproject.toml; do
  service=$(dirname "$project")
  if [ -n "$only_with" ] && [ ! -e "$service/$only_with" ]; then
    continue
  fi
  uv run --frozen --directory "$service" "$@"
done
