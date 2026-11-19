-- no-transaction
CREATE INDEX CONCURRENTLY ix_holds_held_by_owner ON holds (event_id, owner_id) WHERE status = 'held';
