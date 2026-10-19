from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from clipforge.api.errors import install_error_handlers
from clipforge.api.middleware import RequestContextMiddleware
from clipforge.api.v1 import auth, health
from clipforge.auth.rate_limit import FailedAttemptLimiter
from clipforge.config import Settings, get_settings
from clipforge.db.session import create_engine, create_session_factory
from clipforge.logging import configure_logging

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.database_url is None:
            yield
            return
        engine = create_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.db_pool_size,
            disable_prepared_statements=settings.db_disable_prepared_statements,
        )
        app.state.session_factory = create_session_factory(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="ClipForge Bot",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = None
    app.state.auth_limiter = FailedAttemptLimiter(
        max_failures=settings.auth_max_failures,
        window_seconds=settings.auth_failure_window_seconds,
    )
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)
    return app


configure_logging(get_settings().log_level)
app = create_app()
