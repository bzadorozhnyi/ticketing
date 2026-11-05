#!/usr/bin/env bash
# Fails on a breaking API change. A pull request is compared with main, main with its previous commit.
set -euo pipefail

base=$(git merge-base origin/main HEAD)
if [ "$base" = "$(git rev-parse HEAD)" ]; then
  base=$(git rev-parse HEAD~1)
fi

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

for contract in contracts/*.openapi.json; do
  if ! git cat-file -e "$base:$contract" 2>/dev/null; then
    continue
  fi
  name=$(basename "$contract")
  git show "$base:$contract" > "$workdir/base-$name"
  cp "$contract" "$workdir/revision-$name"
  echo "Checking $contract"
  docker run --rm -v "$workdir:/specs:ro" tufin/oasdiff:v1.35.0 \
    breaking "/specs/base-$name" "/specs/revision-$name" --fail-on ERR
done
