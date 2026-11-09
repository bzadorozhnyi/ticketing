from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio.engine import AsyncConnection


def provider_sessions(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    """Sessions for the mock provider on the connection of the test. A commit there is
    a savepoint, so the provider's record is rolled back with the test; the separate
    commit of a real provider is checked by `tests_e2e`.
    """
    connection = session.bind
    assert isinstance(connection, AsyncConnection)
    return async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
