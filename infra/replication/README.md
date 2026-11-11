# Replication

booking's streaming read replica, see ADR 0007. The primary is `booking-db`, the replica `booking-db-replica`.

| File | Used by | What it does |
|---|---|---|
| `pg_hba.conf` | `booking-db` | lets the `replicator` role connect for replication over the compose network |
| `setup.sql` | `booking-db-replication`, once per `up` | creates the `replicator` role and the `booking_replica` slot if missing |
| `replica.sh` | `booking-db-replica` | first start: copies the primary with `pg_basebackup -R`; later starts: just Postgres |

```
# on the primary: is the replica streaming, and how far behind is it?
docker compose exec booking-db psql -U postgres -c "select state, sync_state, replay_lag from pg_stat_replication"
# WAL the slot keeps for the replica
docker compose exec booking-db psql -U postgres -c "select slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) from pg_replication_slots"
# on the replica: it is a standby
docker compose exec booking-db-replica psql -U postgres -c "select pg_is_in_recovery()"

# lag on purpose: apply every transaction 3 s late, then undo
docker compose exec booking-db-replica psql -U postgres -c "alter system set recovery_min_apply_delay = '3s'" -c "select pg_reload_conf()"
docker compose exec booking-db-replica psql -U postgres -c "alter system reset recovery_min_apply_delay" -c "select pg_reload_conf()"
# stop applying altogether, then go on
docker compose exec booking-db-replica psql -U postgres -c "select pg_wal_replay_pause()"
docker compose exec booking-db-replica psql -U postgres -c "select pg_wal_replay_resume()"
```

To make the replica again from scratch: `docker compose rm -sf booking-db-replica`, `docker volume rm ticketing_booking-db-replica-data`,
then `docker compose up -d booking-db-replica`. A replica removed for good must have its slot dropped on the primary
(`select pg_drop_replication_slot('booking_replica')`), or the primary keeps WAL for it until its disk is full.
