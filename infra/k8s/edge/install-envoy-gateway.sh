#!/usr/bin/env bash
# Installs Envoy Gateway (its controller in envoy-gateway-system, the Gateway API and Envoy Gateway CRDs) from the
# release's install.yaml, checked against its sha256 first: 4 MB of YAML that nobody reviews line by line.
set -euo pipefail

version=v1.10.0
sha256=f87f4d59b983aa760da52a2a0002c612028106fe69beefdf3d30cfb03905944e

manifest=$(mktemp)
trap 'rm -f "$manifest"' EXIT
curl -sSfL -o "$manifest" "https://github.com/envoyproxy/gateway/releases/download/$version/install.yaml"
echo "$sha256  $manifest" | shasum -a 256 -c - > /dev/null
# The certgen Job's template cannot change: a new version would be refused without deleting the old Job first.
kubectl -n envoy-gateway-system delete job eg-gateway-helm-certgen --ignore-not-found > /dev/null
kubectl apply --server-side -f "$manifest" > /dev/null
# Objects of the new kinds are refused until their CRDs are served.
kubectl wait --for=condition=Established --timeout=60s crd --all > /dev/null
