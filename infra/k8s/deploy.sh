#!/usr/bin/env bash
# Builds the services' images, loads them into the kind cluster and applies infra/k8s in order:
# platform (stores and config) -> migrations -> seed -> apps, waiting for each before the next.
# Kubernetes keeps no order between resources, so this script does what depends_on did in compose.
set -euo pipefail

cd "$(dirname "$0")"
cluster=ticketing
services=(catalog booking payment seat-hold gateway)

# A new tag for every change, so a Deployment sees a new image and rolls it out.
tag=$(git rev-parse --short HEAD)
if [ -n "$(git status --porcelain -- ../../services)" ]; then
  tag="$tag-$(date +%s)"
fi

kind get clusters | grep -qx "$cluster" || kind create cluster --config kind-cluster.yaml

for service in "${services[@]}"; do
  echo "Image ticketing/$service:$tag"
  docker build --quiet --tag "ticketing/$service:$tag" "../../services/$service" >/dev/null
  kind load docker-image "ticketing/$service:$tag" --name "$cluster" >/dev/null
done

# The manifests name images without a tag; this one is what was just built.
with_tag() {
  sed -E "s#image: (ticketing/[a-z-]+)\$#image: \1:$tag#"
}

kubectl apply -k platform
for statefulset in $(kubectl -n ticketing get statefulset -o name); do
  kubectl -n ticketing rollout status "$statefulset" --timeout=300s
done

# A Job's template cannot change, and every deploy runs its migrations again: delete, then create.
for phase in migrations seed; do
  kubectl kustomize "$phase" | kubectl delete --ignore-not-found --wait -f -
  kubectl kustomize "$phase" | with_tag | kubectl apply -f -
  kubectl kustomize "$phase" | kubectl wait --for=condition=complete --timeout=300s -f -
done

kubectl kustomize apps | with_tag | kubectl apply -f -
for deployment in $(kubectl -n ticketing get deployment -o name); do
  kubectl -n ticketing rollout status "$deployment" --timeout=300s
done
