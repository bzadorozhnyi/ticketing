from fastapi import APIRouter

from events.endpoints.internal.routes.router import router as event_internal_router
from events.endpoints.v1.routes.router import router as event_v1_router

router = APIRouter(prefix="/api")
router.include_router(event_v1_router)

# Not routed by the gateway: only services inside the network call it.
internal_router = APIRouter(prefix="/internal")
internal_router.include_router(event_internal_router)
