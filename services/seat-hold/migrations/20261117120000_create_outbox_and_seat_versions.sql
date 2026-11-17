-- Every change of a seat takes the next number while its row is locked, so a later change of the same seat always has a
-- larger one. Its event carries it, and a reader keeps the seat's latest by it. Rows from before have none.
CREATE SEQUENCE seat_versions;
ALTER TABLE holds ADD COLUMN version BIGINT;

-- Events written in the transaction that changes the seats; the relay publishes them in order and deletes them.
CREATE TABLE outbox (
    id BIGSERIAL PRIMARY KEY,
    routing_key TEXT NOT NULL,
    body JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
