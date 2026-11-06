ALTER TABLE holds
    ADD COLUMN status TEXT NOT NULL DEFAULT 'held'
    CONSTRAINT ck_holds_status_known CHECK (status IN ('held', 'confirmed'));
