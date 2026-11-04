from typing import Any

from pydantic import BaseModel

from core.exceptions import ErrorCode


class ErrorOut(BaseModel):
    code: ErrorCode
    message: str
    details: list


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorOut} for code in codes}
