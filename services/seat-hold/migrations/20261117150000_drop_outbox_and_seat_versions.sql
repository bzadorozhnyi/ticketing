-- The events and their versions served only catalog's copy of availability, which was withdrawn (ADR 0015).
-- No code reads these any more. Dropping the column locks holds briefly; the migration's lock timeout and
-- retries keep it from queueing the service's queries behind it.
DROP TABLE outbox;
ALTER TABLE holds DROP COLUMN version;
DROP SEQUENCE seat_versions;
