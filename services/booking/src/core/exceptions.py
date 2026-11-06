from enum import StrEnum, auto


class ErrorCode(StrEnum):
    INTERNAL_ERROR = auto()
    NOT_FOUND = auto()
    UNAUTHORIZED = auto()
    CONFLICT = auto()
    SERVICE_UNAVAILABLE = auto()
    VALIDATION_ERROR = auto()
    HTTP_ERROR = auto()


class AppException(Exception):
    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(self, msg: str, details: list | None = None):
        self.msg = msg
        self.details = details or []
        super().__init__(msg)


class NotFoundException(AppException):
    code = ErrorCode.NOT_FOUND


class UnauthorizedException(AppException):
    code = ErrorCode.UNAUTHORIZED


class ConflictException(AppException):
    code = ErrorCode.CONFLICT


class ValidationException(AppException):
    code = ErrorCode.VALIDATION_ERROR


class InternalServerException(AppException):
    code = ErrorCode.INTERNAL_ERROR


class ServiceUnavailableException(AppException):
    code = ErrorCode.SERVICE_UNAVAILABLE
