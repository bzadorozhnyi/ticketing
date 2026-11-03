from enum import StrEnum, auto


class ErrorCode(StrEnum):
    INTERNAL_ERROR = auto()
    NOT_FOUND = auto()
    CONFLICT = auto()


class AppException(Exception):
    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(self, msg: str, details: list | None = None):
        self.msg = msg
        self.details = details or []
        super().__init__(msg)


class NotFoundException(AppException):
    code = ErrorCode.NOT_FOUND


class ConflictException(AppException):
    code = ErrorCode.CONFLICT


class InternalServerException(AppException):
    code = ErrorCode.INTERNAL_ERROR
