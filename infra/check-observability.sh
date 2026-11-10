#!/usr/bin/env bash
# Checks the observability overlay: the Prometheus config, the unit tests of the SLO rules
# (infra/observability/rules/slo.test.yaml) and that every dashboard is valid JSON.
# promtool comes from the Prometheus image the overlay runs, so the versions cannot differ.
set -euo pipefail

cd "$(dirname "$0")/observability"
image=$(grep -o 'prom/prometheus:[^" ]*' compose.yml)

for dashboard in grafana/dashboards/*.json; do
  python3 -m json.tool "$dashboard" > /dev/null || { echo "Invalid JSON: $dashboard"; exit 1; }
done
docker run --rm -v "$PWD:/w:ro" -w /w --entrypoint promtool "$image" check config prometheus.yaml
docker run --rm -v "$PWD:/w:ro" -w /w/rules --entrypoint promtool "$image" test rules slo.test.yaml
