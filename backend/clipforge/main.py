from fastapi import FastAPI

from clipforge.api.v1 import health
from clipforge.config import get_settings
from clipforge.logging import configure_logging

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    app = FastAPI(title="ClipForge Bot", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(health.router, prefix=API_PREFIX)
    return app


configure_logging(get_settings().log_level)
app = create_app()
