from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import text

from core.db import SessionDep

router = APIRouter(prefix="/_probe", include_in_schema=False)


@router.get("/db")
async def query_db(session: SessionDep) -> None:
    await session.execute(text("SELECT 1"))


@router.get("/bad-sql")
async def run_bad_sql(session: SessionDep) -> None:
    await session.execute(text("SELECT * FROM no_such_table"))


@router.get("/error")
async def fail() -> None:
    raise ValueError("secret internals")


@router.get("/validated")
async def validated(limit: Annotated[int, Query(ge=1)]) -> None:
    return None
