from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

import core.health  # noqa: F401
import tools.events  # noqa: F401
import tools.holds  # noqa: F401
import tools.orders  # noqa: F401
from core.logging import configure_logging
from server import mcp

configure_logging()


def create_app() -> Starlette:
    """Stateless JSON answers: any pod serves any request, and the gateway, which
    buffers bodies, passes one JSON document instead of a stream. A new app each call:
    its session manager runs only once.

    No DNS rebinding protection: it guards a server listening on a user's localhost,
    which a web page could reach. This one has no published port, every request comes
    through the gateway with a bearer token a page cannot add, and the gateway passes
    the client's Host header, which the check would refuse.
    """
    return mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )


app = create_app()
