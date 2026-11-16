#!/usr/bin/env bash
# Installs the CloudNativePG operator (its controller in cnpg-system and its CRDs) from the release's manifest, checked
# against its sha256 first: 1.3 MB of YAML that nobody reviews line by line. The operator runs booking's Postgres
# (platform/booking-db.yaml) and promotes a replica when the primary fails, see ADR 0012.
set -euo pipefail

version=1.30.1
sha256=37237f145d8138256ea25ae830f87759255665ff08f8d552fdd8224a5ec032fb

manifest=$(mktemp)
trap 'rm -f "$manifest"' EXIT
curl -sSfL -o "$manifest" "https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v$version/cnpg-$version.yaml"
echo "$sha256  $manifest" | shasum -a 256 -c - > /dev/null
# The CRDs are too large for the annotation a client-side apply writes.
kubectl apply --server-side -f "$manifest" > /dev/null
kubectl wait --for=condition=Established --timeout=60s crd --all > /dev/null
# Its webhooks check every Cluster, even in a dry run: one sent before the controller answers is refused.
kubectl -n cnpg-system rollout status deployment/cnpg-controller-manager --timeout=300s > /dev/null
