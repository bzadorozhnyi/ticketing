import events.endpoints.v1.routes.get_event  # noqa: F401
import events.endpoints.v1.routes.list_events  # noqa: F401
from events.endpoints.v1.routes.router import router as event_v1_router

__all__ = ["event_v1_router"]
