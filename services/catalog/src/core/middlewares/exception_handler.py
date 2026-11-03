from fastapi import Request, status
from fastapi.responses import JSONResponse

from core.exceptions import AppException, ErrorCode

_STATUS_CODES: dict[ErrorCode, int] = {
    ErrorCode.INTERNAL_ERROR: status.HTTP_500_INTERNAL_SERVER_ERROR,
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.CONFLICT: status.HTTP_409_CONFLICT,
}


async def app_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppException)
    return JSONResponse(
        status_code=_STATUS_CODES[exc.code],
        content={"code": exc.code, "message": exc.msg, "details": exc.details},
    )
