from fastapi import FastAPI

from core.exceptions import AppException
from core.health import router as health_router
from core.logging import configure_logging
from core.middlewares.exception_handler import app_exception_handler
from routes import router

configure_logging()

app = FastAPI(title="catalog")

app.include_router(router)
app.include_router(health_router)
app.add_exception_handler(AppException, app_exception_handler)
