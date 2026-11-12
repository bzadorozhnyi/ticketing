#!/usr/bin/env bash
# Checks infra/k8s without deploying it: every phase must render, and an empty kind cluster running the node image
# of kind-cluster.yaml must accept every object in a server-side dry run, where the API server rejects an unknown
# field or an invalid value and stores nothing. It does not build images or start a pod.
set -euo pipefail

cd "$(dirname "$0")/k8s"
cluster=ticketing-check
image=$(grep -o 'kindest/node:[^ ]*' kind-cluster.yaml)
phases=(metrics-server platform migrations seed apps)

for phase in "${phases[@]}"; do
  kubectl kustomize "$phase" > /dev/null
done

# A kubeconfig of its own, so the current kubectl context stays on whatever cluster it was.
KUBECONFIG=$(mktemp)
export KUBECONFIG
trap 'kind delete cluster --name "$cluster"; rm -f "$KUBECONFIG"' EXIT
kind create cluster --name "$cluster" --image "$image" --wait 120s

# A dry run creates nothing, so the namespace every other object lives in has to exist for real.
kubectl apply -f platform/namespace.yaml
for phase in "${phases[@]}"; do
  kubectl apply --dry-run=server --validate=strict -k "$phase"
done
