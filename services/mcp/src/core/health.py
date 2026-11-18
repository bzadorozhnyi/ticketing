from starlette.requests import Request
from starlette.responses import JSONResponse

from server import mcp


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/ready", methods=["GET"])
async def ready(_: Request) -> JSONResponse:
    """The services the tools call are not checked: a broken one fails its tools with a
    clear error, and failing here would take every pod out of rotation for one service.
    """
    return JSONResponse({"status": "ok"})
