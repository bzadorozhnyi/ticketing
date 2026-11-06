from collections.abc import Mapping

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from core.exceptions import AppException, ErrorCode

_STATUS_CODES: dict[ErrorCode, int] = {
    ErrorCode.INTERNAL_ERROR: status.HTTP_500_INTERNAL_SERVER_ERROR,
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.UNAUTHORIZED: status.HTTP_401_UNAUTHORIZED,
    ErrorCode.CONFLICT: status.HTTP_409_CONFLICT,
    ErrorCode.VALIDATION_ERROR: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorCode.SERVICE_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _error_response(
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    details: list | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "details": details or []},
        headers=headers,
    )


async def app_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppException)
    return _error_response(
        status_code=_STATUS_CODES[exc.code],
        code=exc.code,
        message=exc.msg,
        details=exc.details,
    )


async def validation_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return _error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code=ErrorCode.VALIDATION_ERROR,
        message="Request validation failed",
        details=jsonable_encoder(exc.errors()),
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    code = (
        ErrorCode.NOT_FOUND
        if exc.status_code == status.HTTP_404_NOT_FOUND
        else ErrorCode.HTTP_ERROR
    )
    return _error_response(
        status_code=exc.status_code,
        code=code,
        message=exc.detail,
        headers=exc.headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code=ErrorCode.INTERNAL_ERROR,
        message="Internal server error",
    )
