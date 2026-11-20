from pydantic import BaseModel

from core.exceptions import ErrorCode


class ErrorOut(BaseModel):
    code: ErrorCode
    message: str
    details: list
