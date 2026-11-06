from fastapi import APIRouter

from orders.endpoints.v1.routes.router import router as order_v1_router

router = APIRouter(prefix="/api")
router.include_router(order_v1_router)
