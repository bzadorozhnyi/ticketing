-- Runs on the primary before every start of the replica; each step is safe to repeat.
-- The role the replica connects as: it may stream WAL, nothing else.
SELECT format('CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD %L', :'password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'replicator') \gexec

-- The primary keeps WAL until the replica has it, so a replica that was down catches up
-- instead of being copied again. A slot whose replica is gone keeps WAL until the disk is full.
SELECT pg_create_physical_replication_slot('booking_replica')
WHERE NOT EXISTS (SELECT FROM pg_replication_slots WHERE slot_name = 'booking_replica');
