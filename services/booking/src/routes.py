from fastapi import APIRouter

from orders.endpoints.internal.routes.router import router as order_internal_router
from orders.endpoints.v1.routes.router import router as order_v1_router

router = APIRouter(prefix="/api")
router.include_router(order_v1_router)

# Not routed by the gateway: only services inside the network call it.
internal_router = APIRouter(prefix="/internal")
internal_router.include_router(order_internal_router)
