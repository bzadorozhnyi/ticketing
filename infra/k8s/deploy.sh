#!/usr/bin/env bash
# Builds the services' images, loads them into the kind cluster and applies infra/k8s in order:
# metrics-server, Envoy Gateway -> platform (stores and config) and observability -> migrations -> seed -> apps -> edge, waiting for each before the next.
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

# migrations and observability read files from outside their directory (infra/rabbitmq, infra/observability).
render() {
  kubectl kustomize --load-restrictor=LoadRestrictionsNone "$1"
}

# The manifests name images without a tag; this one is what was just built.
with_tag() {
  sed -E "s#image: (ticketing/[a-z-]+)\$#image: \1:$tag#"
}

kubectl apply -k metrics-server
edge/install-envoy-gateway.sh
kubectl apply -k platform
render observability | kubectl apply -f -
for statefulset in $(kubectl -n ticketing get statefulset -o name); do
  kubectl -n ticketing rollout status "$statefulset" --timeout=300s
done

# A Job's template cannot change, and every deploy runs its migrations again: delete, then create.
for phase in migrations seed; do
  render "$phase" | kubectl delete --ignore-not-found --wait -f -
  render "$phase" | with_tag | kubectl apply -f -
  kubectl -n ticketing wait --for=condition=complete --timeout=300s job --all
done

kubectl kustomize apps | with_tag | kubectl apply -f -
for deployment in $(kubectl -n ticketing get deployment -o name); do
  kubectl -n ticketing rollout status "$deployment" --timeout=300s
done

# A self-signed certificate for localhost, made once: its key stays in the cluster, never in the repository.
if ! kubectl -n ticketing get secret edge-tls > /dev/null 2>&1; then
  certs=$(mktemp -d)
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj /CN=localhost \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" -keyout "$certs/tls.key" -out "$certs/tls.crt" 2> /dev/null
  kubectl -n ticketing create secret tls edge-tls --cert="$certs/tls.crt" --key="$certs/tls.key"
  rm -rf "$certs"
fi
kubectl apply -k edge
kubectl -n ticketing wait gateway/edge --for=condition=Programmed --timeout=300s
