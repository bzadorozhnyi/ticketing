# 0009: Kubernetes on kind

**Status:** accepted

## Context

M1 to M7 ran in compose. M8 checks what Kubernetes promises (rollouts without failed requests, pods that come back,
autoscaling, CPU limits, TLS at the edge) with the same load experiments, on a laptop with 8 GB for Docker.

## Decision

- **kind**: a cluster whose node is a Docker container, already installed and lighter than minikube. One node.
- **Plain manifests and kustomize** (built into `kubectl`), one directory per phase: `platform`, `migrations`, `seed`,
  `apps`. `infra/k8s/deploy.sh` applies them in that order and waits for each, since Kubernetes keeps no order between
  resources. Helm only for third-party components later (Envoy Gateway, metrics-server).
- **Stores in the cluster** as StatefulSets with their own volume: Postgres per service, RabbitMQ; Valkey keeps nothing,
  so it is a Deployment. The same images and versions as in compose. No operator (CloudNativePG and the like).
- **Migrations are Jobs**, run before the services on every deploy. booking's read replica is not part of it: without
  `DB_REPLICA` its reads go to the primary (ADR 0007).
- **CPU requests, no CPU limits**; memory requests and limits. A CPU limit throttles a process in steps of 100 ms
  (TIK-56); its cost is measured on its own in TIK-60.
- **Readiness on `/ready`, liveness on `/health`**; the workers have no probe and are restarted when they exit.
- Images are loaded into the node with `kind load docker-image`, tagged with the commit.
- RabbitMQ runs as its own user with `fsGroup`: run as root, the image's entrypoint changes the owner of a new volume
  while the server may already read its cookie, and the first start on a new volume failed.

| Option | Why not |
|---|---|
| minikube | Addons and `minikube tunnel` are convenient, but it had to be installed, and kind was there |
| Helm for our services | Templates and values for five services we write ourselves; kustomize keeps plain YAML |
| An initContainer that waits for the migration | Every replica would wait, and Alembic takes no lock against two replicas migrating at once |
| Postgres outside the cluster (the compose one) | Two places to start and stop; killing a database pod is one of the M8 experiments |

## Consequences

- One script deploys everything in about two minutes; the cluster uses about 1.8 GB at rest.
- The compose stack and the cluster share `localhost:8000`: one runs at a time.
- A deploy restarts every service whose image changed, all at once; rolling them without failed requests is TIK-71.
