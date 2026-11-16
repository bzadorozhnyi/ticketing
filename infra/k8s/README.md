# Kubernetes (kind)

Every service, store and migration in a local kind cluster, see ADR 0009. The compose stack and this cluster both use
`localhost:8000`, so run one at a time: `docker compose down` first (volumes stay).

```
infra/k8s/deploy.sh                       # creates the cluster if needed, builds and loads the images, deploys;
                                          # it uses its own kubeconfig, so the current kubectl context does not matter
kubectl config current-context            # kind-ticketing; your other clusters keep their own contexts
kubectl -n ticketing get pods             # every pod and its state
TOKEN=$(kubectl -n ticketing exec deploy/gateway -- issue-token user-1)
kubectl -n ticketing get secret edge-tls -o jsonpath='{.data.tls\.crt}' | base64 -d > edge.crt   # the self-signed certificate
curl --cacert edge.crt -H "Authorization: Bearer $TOKEN" https://localhost:8000/api/v1/events   # --http2 works too
kind delete cluster --name ticketing      # everything goes, the databases too
```

| Directory | What | Applied |
|---|---|---|
| `metrics-server/` | metrics-server v0.9.1 in `kube-system`: CPU and memory of pods for the HPA and `kubectl top` | first |
| `cnpg/` | the CloudNativePG operator v1.30.1 in `cnpg-system`, from `install-cnpg.sh`: it runs booking's Postgres | before `platform` |
| `platform/` | namespace, secrets, config maps, Postgres per service, RabbitMQ, Valkey | then; `deploy.sh` waits for every StatefulSet and for booking's database |
| `observability/` | collector, Tempo, Prometheus, Grafana, built from `infra/observability` | with `platform` |
| `migrations/` | one Job per service: `alembic upgrade head`, or `migrate` for seat-hold; and RabbitMQ's policies (`infra/rabbitmq/policies.sh`) | then; waits for every Job to complete |
| `seed/` | catalog's demo data, a Job | then |
| `apps/` | the services and their workers, Deployments and Services | then; waits for every rollout |
| `edge/` | Envoy Gateway's `Gateway` (HTTPS) and `HTTPRoute` to the gateway; Envoy Gateway itself from `install-envoy-gateway.sh` | last, as it takes the gateway's old NodePort; waits until Envoy has the config |

Kubernetes keeps no order between resources; `deploy.sh` does what `depends_on` did in compose. Every deploy tags the
images with the commit (and a timestamp when `services/` has local changes), so a Deployment sees a new image and
rolls it out; Jobs are deleted and created again, since a Job's template cannot change.

A rollout starts the new pod, waits for its readiness probe, then stops the old one (`replicas: 1` with the default
strategy). Stopping a pod removes it from its Service and sends SIGTERM at the same time, so for a moment new connections
still reach a process that has closed its socket. Every HTTP container has `preStop: sleep 5`: it keeps serving while the
Service stops routing to it, and SIGTERM comes after. Connections the gateway already holds to the old pod are not
covered: a request sent on one as it closes gets 502, which the gateway retries only for `GET` and `HEAD`.
Every HTTP service runs two pods, so one can die while the other serves; each has a PodDisruptionBudget with
`minAvailable: 1`, so a voluntary eviction such as `kubectl drain` waits rather than stop the last ready pod. A budget
does nothing for a crash or a `kubectl delete pod`, and with one replica it would block a drain for good.
catalog scales itself: a HorizontalPodAutoscaler keeps 2 to 6 pods at 70 % of their CPU request (`kubectl get hpa`),
from metrics-server. It checks every 15 s, adds pods at once and removes them 5 minutes after the load falls. Its CPU
request is one core, all that one uvicorn process can use, so one step adds at most 1.43 times the pods. The manifest
has no `replicas`, or every `kubectl apply` would undo the autoscaler; the first apply without the field set it to 1.
A new pod gets only new connections; the gateway opens all of its connections again every 10 s
(`CLIENT__POOL_MAX_AGE_SECONDS`), so a new pod takes load within that time.
The liveness probe asks `/health` (the process answers), never `/ready` (its database answers): a database outage takes
the pods out of their Service but does not restart them. The workers run one pod each: two saga workers or two relays at once are safe (`FOR UPDATE SKIP LOCKED`), each one finishes its step on SIGTERM,
and while one is down orders wait instead of failing.

CI runs `infra/check-k8s.sh`: every directory renders, and a throwaway kind cluster with the same node image accepts
every object in a server-side dry run. It catches a misspelled field or an invalid value, not a pod that fails to start.
The node image is pinned by digest in `kind-cluster.yaml`; a cluster made before the pin keeps its own image.

Observability, the same files as the compose overlay (ADR 0006): every service exports to `otel-collector`, and
Prometheus also reads every container's CPU, throttling and memory from the kubelet (`scrape/kubelet.yaml`), and
Envoy's requests from its pods (`scrape/envoy.yaml`): an availability SLO at the edge sees the 503s Envoy answers
itself, which never reach the gateway. `infra/check-observability.sh` fails when these manifests run another image
than compose.

booking's Postgres is a CloudNativePG `Cluster` of three instances (ADR 0012): a primary behind the Service
`booking-db-rw` and two replicas behind `booking-db-ro`. The operator promotes a replica when the primary fails and moves
the Services with it; the pods are `booking-db-1` to `-3`, and which is the primary changes.

```
kubectl -n ticketing get cluster booking-db                      # instances, ready ones, the current primary
kubectl -n ticketing get pods -l cnpg.io/cluster=booking-db -L role
kubectl -n ticketing exec booking-db-1 -c postgres -- psql -U postgres -c "select application_name, state from pg_stat_replication"
```

Replication is synchronous: a commit waits until one replica has it, so a failover loses no confirmed order. With both
replicas down booking's writes wait. If the replicas stay `async` after synchronous replication is turned on in a
running cluster (`select sync_state from pg_stat_replication`), the primary has not reloaded its configuration; it
happened once (TIK-96), and `select pg_reload_conf()` on the primary fixed it.

A replica that ended on a timeline the primary does not have (after a primary hung and woke, TIK-97) retries for good
while the cluster reports itself healthy; its log repeats "forked off", "is not a child of" or "not in this server's
history", and `BookingReplicaNotStreaming` fires. Rebuild it at once: the next failover may promote it, and booking
then loses everything since its timeline split (TIK-93). Delete its volume and pod, and the operator copies the primary
into a new one (about 25 s here):

```
kubectl -n ticketing delete pvc booking-db-2 --wait=false
kubectl -n ticketing delete pod booking-db-2
```

A cluster created before it still has booking-db as a StatefulSet; delete it with its Service and volume once
(`kubectl -n ticketing delete statefulset/booking-db service/booking-db pvc/data-booking-db-0`), then deploy. Its
orders go with it.

Prometheus keeps 31 days for the 30-day SLOs, in a 4Gi volume. A StatefulSet's volume template cannot change, so a
cluster created with the old 2Gi refuses the apply (`Forbidden: updates to statefulset spec`). Delete the StatefulSet
but not its pod or volume, then deploy; the data stays. kind's volumes do not enforce their size, so the old volume
keeps working.

```
kubectl -n ticketing delete statefulset prometheus --cascade=orphan
infra/k8s/deploy.sh
```

```
kubectl -n ticketing port-forward service/grafana 3000:3000        # http://localhost:3000, the Containers dashboard
kubectl -n ticketing port-forward service/prometheus 9090:9090
```

Tempo needs more than 512 MiB: it read about 1 GiB back from its write-ahead log after a restart, and at 512 MiB it was
killed while doing so, again and again; the collector then held what it could not send and spent 7.5 cores on garbage
collection, slowing the whole node. Every request is traced, so tracing grows with the traffic.

Looking inside:

```
kubectl -n ticketing describe pod <name>              # events at the bottom: why a pod does not start or restarts
kubectl -n ticketing logs deployment/booking-saga     # logs; --previous for the container before a restart
kubectl -n ticketing exec -it catalog-db-0 -- psql -U postgres -d catalog_db
kubectl -n ticketing port-forward service/catalog 8001:8000   # a tunnel from localhost:8001 while it runs
```

How a request from the Mac reaches the gateway: kind maps `127.0.0.1:8000` to port 30080 of its node container
(`kind-cluster.yaml`), Envoy's Service is a NodePort on 30080, and Envoy ends TLS (HTTP/1.1 or HTTP/2, ALPN) and sends
each request to a gateway pod of its own choice, over HTTP/1.1 (ADR 0011). The gateway's Service is a ClusterIP.
Inside the cluster services call each other by their Service names, as in compose: kube-proxy balances connections,
not requests, which is why the gateway renews its upstream connections every 10 s and nothing inside speaks HTTP/2.
