CREATE TABLE holds (
    event_id UUID NOT NULL,
    seat_id UUID NOT NULL,
    hold_id UUID NOT NULL,
    owner_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (event_id, seat_id)
);

CREATE INDEX ix_holds_hold_id ON holds (hold_id);
