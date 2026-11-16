# 0012: booking's Postgres fails over by itself, run by CloudNativePG

**Status:** accepted

## Context

booking holds the orders and the saga's state. Its Postgres had one asynchronous replica in compose and none in the
cluster (ADR 0007, ADR 0009). When the primary died nothing promoted a replica: booking answered 503, the saga stopped,
and it stayed so until someone started the database again. Failing over by hand, or with a script around
`pg_promote()`, risks two primaries at once (split brain), and writes to both cannot be merged.

## Decision

- **CloudNativePG 1.30.1** runs booking's Postgres in the cluster (supports Kubernetes 1.34 to 1.36 and Postgres 14 to
  18): a `Cluster` of three instances, one primary and two streaming replicas, Postgres 18.7 pinned by digest
  (`infra/k8s/platform/booking-db.yaml`). It is installed from the release's manifest, checked against its sha256
  (`infra/k8s/cnpg/install-cnpg.sh`), like Envoy Gateway (ADR 0011).
- **The operator owns the topology.** It keeps the Service `booking-db-rw` on the primary and `booking-db-ro` on the
  replicas, a replication slot per replica on the primary, and promotes a replica when the primary is unhealthy. It
  keeps its state in the Kubernetes API, so it needs no etcd or Consul of its own.
- **booking writes to `booking-db-rw`** and its API reads orders from `booking-db-ro` (ADR 0007). It logs in as the
  database's owner, `booking`, whose random password the operator keeps in the Secret `booking-db-app`; the superuser
  is off. The owner has `pg_monitor`, given through the operator's managed roles, so the replication probe sees the
  replicas' state.
- **Synchronous replication, quorum of one** (`method: any`, `number: 1`): a commit is confirmed once either replica
  has its WAL on disk. Asynchronous replication came first, to measure what it loses (TIK-95); synchronous replaced it
  (TIK-96). Without failover (ADR 0007) a dead primary came back with its own disk and lost nothing; failing over to a
  replica is what turns the lag into lost commits.
- **`dataDurability: required`**: with no replica answering, writes wait. `preferred` would fall back to asynchronous
  commits, but it did not help when the replicas hung (writes waited all the same) and only after about 30 s when they
  stopped, and it brings the loss back for that time.
- **`failoverQuorum`**: the operator promotes a replica only when the replicas it can reach must include one with
  every confirmed commit (R + W > N). With the primary and one replica gone it waits instead.
- **A stopping instance ends its sessions at once** (`smartShutdownTimeout: 0`, a fast shutdown). With the default
  smart shutdown (180 s) a primary that woke after its failover went on committing on the sessions already open for
  about 40 s, and a replica confirmed those commits: two primaries (TIK-97). With the fast one the same test lost
  nothing in 3 runs (TIK-98). A planned stop of a primary rolls back its unfinished transactions; booking repeats them
  with their keys.
- **The quorum size W is a trade between writes and failover**, not a safety margin: `failoverQuorum` already keeps
  every confirmed commit with W = 1. With N replicas, writes go on with N - W of them down, and a failover can happen
  with W - 1 of them down beside the primary; the two always add up to N - 1. A larger W also makes each commit wait for
  the W-th fastest replica.

  | Replicas N | W | Writes survive replicas down | Failover survives replicas down beside the primary |
  |---|---|---|---|
  | 2 (now) | 1 | 1 | 0 |
  | 2 | 2 | 0 | 1 |
  | 3 | 2 | 1 | 1 |
  | 4 | 2 | 2 | 1 |

  With two replicas W = 1 is the only choice that lets writes survive one replica down. More instances with W left at 1
  would need every replica reachable to fail over, so W is revisited with `instances`; a majority balances the two.
- **Only booking.** catalog, payment and seat-hold keep one Postgres each, a StatefulSet. payment's ledger would matter
  as much, but one database is enough to learn failover on, and booking's saga is where a lost write meets the other
  services.

| Option | Why not |
|---|---|
| Patroni 4.1.5 | Needs a store for its state (etcd, Consul or the Kubernetes API) and an image of Postgres with Patroni in it; more parts to run for the same result |
| A script calling `pg_promote()` | It cannot be sure the old primary is gone before promoting the new one: split brain |
| Asynchronous replication | A failover lost 28 and 29 confirmed orders, 26 of them charged (TIK-95) |
| `dataDurability: preferred` | Writes still waited when the replicas hung, and when it gives way the loss comes back |
| Synchronous with both replicas (`number: 2`) | One replica down would stop every write |

## Consequences

- Compose keeps one replica without failover; only the cluster fails over.
- kind runs on one node, so all three instances share it: a pod or a process can fail, a node cannot.
- The replication probe reports every physical slot, labelled by `db.replication.slot`, and `BookingReplicaNotStreaming`
  compares them one by one: with two replicas, one down beside one streaming still pages.
- A cluster made before this change keeps its StatefulSet, Service and volume of booking-db until they are deleted
  (`infra/k8s/README.md`).
- A failover takes about 20 s (TIK-95): 2 s until the old primary's container stops, 15 s for its lease to run out,
  then the promotion. Clients that repeat get their orders.
- Asynchronous replication lost what the replicas had not received: with replicas 5 s behind, 28 and 29 orders that
  had been answered 202 vanished, and 26 of them had been charged and their seats confirmed in the other services.
  Synchronous replication lost none in the same test (TIK-96).
- A synchronous commit costs a round trip to the nearest replica: 0.35 to 0.57 ms on kind's one node, 0.33 to 1.7 ms
  with 1 ms added towards the replicas. Buyers did not see it: `POST /orders` stayed at about 8 to 10 ms at the median.
- One replica down changes nothing. Both down or hung stop booking's writes until one returns: the gateway answers
  504 after its 10 s, then the clients' repeats meet its bulkhead (503). A commit that waited is kept, and a repeat with
  the same key returns it.
- If the primary and a replica fail together and the primary does not come back, booking takes no writes until a
  person promotes the replica (`kubectl cnpg promote`) and accepts what it may lack.
- A primary that hangs longer than the failover takes (about 57 s here) wakes into kubelet's SIGTERM. With a smart
  shutdown it served the sessions already open for about 40 s, booking's pooled connections were still there, and a
  replica followed it and confirmed its commits: 478 and 480 confirmed orders lost in 2 runs (TIK-97). With a fast
  shutdown none were lost in 3 runs (TIK-98). Shorter hangs (10 and 25 s) caused no failover and lost nothing.
- A frozen pod's kernel still completes TCP handshakes, so a connection opened to the old primary while it was frozen
  waits and is served when it wakes; a replica connects without a connect timeout, which the operator sets. Even with
  the fast shutdown, a replica ended on the old primary's timeline in 1 of 3 runs and had to be rebuilt by deleting its
  volume (`infra/k8s/README.md`).
- A replica whose WAL receiver hangs holds the failover until it ends: no promotion and no new primary pod for as long
  as it lasts (TIK-97). A replica hung as a whole, beside a lost primary, stops the promotion too, through the quorum.
