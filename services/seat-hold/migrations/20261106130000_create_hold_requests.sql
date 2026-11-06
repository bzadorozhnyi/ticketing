CREATE TABLE hold_requests (
    owner_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    hold_id UUID NOT NULL,
    event_id UUID NOT NULL,
    seat_ids UUID[] NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, idempotency_key)
);

CREATE INDEX ix_hold_requests_created_at ON hold_requests (created_at);
