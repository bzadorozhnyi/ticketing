from typing import Annotated

from fastapi import Depends, Header

from core.exceptions import UnauthorizedException


def get_user_id(x_user_id: Annotated[str | None, Header()] = None) -> str:
    """The caller, as the gateway reports it. Services trust this header because
    they are reachable only through the gateway.
    """
    if not x_user_id:
        raise UnauthorizedException("X-User-Id is missing")
    return x_user_id


UserIdDep = Annotated[str, Depends(get_user_id)]
