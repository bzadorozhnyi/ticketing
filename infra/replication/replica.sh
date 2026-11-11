#!/bin/sh
# A streaming replica of booking-db. The first start copies the primary with pg_basebackup;
# -R writes standby.signal and primary_conninfo, so Postgres then starts as a read-only standby.
set -eu

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  pg_basebackup -h booking-db -U replicator -D "$PGDATA" -R -X stream -S booking_replica
fi
exec postgres "$@"
