# Kubernetes (kind)

Every service, store and migration in a local kind cluster, see ADR 0009. The compose stack and this cluster both use
`localhost:8000`, so run one at a time: `docker compose down` first (volumes stay).

```
infra/k8s/deploy.sh                       # creates the cluster if needed, builds and loads the images, deploys
kubectl config current-context            # kind-ticketing; your other clusters keep their own contexts
kubectl -n ticketing get pods             # every pod and its state
TOKEN=$(kubectl -n ticketing exec deploy/gateway -- issue-token user-1)
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/events
kind delete cluster --name ticketing      # everything goes, the databases too
```

| Directory | What | Applied |
|---|---|---|
| `platform/` | namespace, secrets, config maps, Postgres per service, RabbitMQ, Valkey | first; `deploy.sh` waits for every StatefulSet |
| `migrations/` | one Job per service: `alembic upgrade head`, or `migrate` for seat-hold | then; waits for every Job to complete |
| `seed/` | catalog's demo data, a Job | then |
| `apps/` | the services and their workers, Deployments and Services | last; waits for every rollout |

Kubernetes keeps no order between resources; `deploy.sh` does what `depends_on` did in compose. Every deploy tags the
images with the commit (and a timestamp when `services/` has local changes), so a Deployment sees a new image and
rolls it out; Jobs are deleted and created again, since a Job's template cannot change.

CI runs `infra/check-k8s.sh`: every directory renders, and a throwaway kind cluster with the same node image accepts
every object in a server-side dry run. It catches a misspelled field or an invalid value, not a pod that fails to start.
The node image is pinned by digest in `kind-cluster.yaml`; a cluster made before the pin keeps its own image.

Looking inside:

```
kubectl -n ticketing describe pod <name>              # events at the bottom: why a pod does not start or restarts
kubectl -n ticketing logs deployment/booking-saga     # logs; --previous for the container before a restart
kubectl -n ticketing exec -it catalog-db-0 -- psql -U postgres -d catalog_db
kubectl -n ticketing port-forward service/catalog 8001:8000   # a tunnel from localhost:8001 while it runs
```

How a request from the Mac reaches the gateway: kind maps `127.0.0.1:8000` to port 30080 of its node container
(`kind-cluster.yaml`), and the gateway's Service is a NodePort on 30080. Inside the cluster services call each other by
their Service names, as in compose.
