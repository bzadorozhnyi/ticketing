#!/usr/bin/env bash
# Checks the observability overlay: the collector and both Prometheus configs (compose and the cluster), the unit tests of the SLO rules
# (infra/observability/rules/slo.test.yaml) and that every dashboard is valid JSON.
# The tools come from the images the overlay runs, so the versions cannot differ.
set -euo pipefail

cd "$(dirname "$0")/observability"
image=$(grep -o 'prom/prometheus:[^" ]*' compose.yml)
collector=$(grep -o 'otel/opentelemetry-collector:[^" ]*' compose.yml)

for dashboard in grafana/dashboards/*.json; do
  python3 -m json.tool "$dashboard" > /dev/null || { echo "Invalid JSON: $dashboard"; exit 1; }
done
docker run --rm -v "$PWD:/w:ro" "$collector" validate --config=/w/otel-collector.yaml
# The cluster's config reads a service account token, which exists only in a pod: an empty file stands in for it.
token=$(mktemp)
trap 'rm -f "$token"' EXIT
for config in prometheus.yaml prometheus-k8s.yaml; do
  docker run --rm -v "$PWD:/w:ro" -v "$token:/var/run/secrets/kubernetes.io/serviceaccount/token:ro" -w /w \
    --entrypoint promtool "$image" check config "$config"
done
docker run --rm -v "$PWD:/w:ro" -w /w/rules --entrypoint promtool "$image" test rules slo.test.yaml
