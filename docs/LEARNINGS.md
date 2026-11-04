# Learnings

## M1

- A test that passes is not proof. Mutation checks showed that a tie-breaker test passed without the tie-breaker, because UUIDv7 order matched insertion order.
- UUIDv7 is time-ordered, so tests on sort order must insert rows in reverse order.
- Alembic autogenerate can produce a migration that fails on a real database: it created a composite foreign key before the unique constraint it needs, and added a `NOT NULL` column without a default.
- If a model is missing from the registry, autogenerate proposes dropping its table. An in-process check misses this, because the app has already imported the model.
- `ForeignKey` on a column covers one column only. A composite guarantee needs a table-level `ForeignKeyConstraint`.
- A session joined to an outer test transaction rolls that transaction back on failure unless it uses `join_transaction_mode="create_savepoint"`.
- A healthy `/ready` does not mean clean failures: during a database outage the API still returned a plain-text 500, and the first request after a database restart failed on a stale pooled connection.
- Connection failures from asyncpg arrive as plain `OSError` subclasses, not SQLAlchemy errors, so `except OperationalError` would catch none of them.
- Starlette re-raises after a handler for `Exception` runs, so logging in that handler duplicates the traceback uvicorn already writes.
