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
- **Asynchronous replication first**, as the operator's default: what a failover loses and how long it takes are
  measured before synchronous replication is tried (TIK-95, TIK-96).
- **Only booking.** catalog, payment and seat-hold keep one Postgres each, a StatefulSet. payment's ledger would matter
  as much, but one database is enough to learn failover on, and booking's saga is where a lost write meets the other
  services.

| Option | Why not |
|---|---|
| Patroni 4.1.5 | Needs a store for its state (etcd, Consul or the Kubernetes API) and an image of Postgres with Patroni in it; more parts to run for the same result |
| A script calling `pg_promote()` | It cannot be sure the old primary is gone before promoting the new one: split brain |
| Synchronous replication from the start | Nothing to compare it with: the loss of the asynchronous case is the reason to pay for it |

## Consequences

- Compose keeps one replica without failover; only the cluster fails over.
- kind runs on one node, so all three instances share it: a pod or a process can fail, a node cannot.
- The replication probe reports every physical slot, labelled by `db.replication.slot`, and `BookingReplicaNotStreaming`
  compares them one by one: with two replicas, one down beside one streaming still pages.
- A cluster made before this change keeps its StatefulSet, Service and volume of booking-db until they are deleted
  (`infra/k8s/README.md`).
- The failover itself, what it loses and how long it takes, is measured in TIK-95 to TIK-97.
