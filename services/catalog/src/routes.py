from fastapi import APIRouter

from events.endpoints.v1.routes import event_v1_router

router = APIRouter(prefix="/api")
router.include_router(event_v1_router)
