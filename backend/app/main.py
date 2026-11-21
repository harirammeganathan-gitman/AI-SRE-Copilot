import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.app.application.container import ServiceContainer
from backend.app.application.exceptions import ApplicationError
from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import configure_logging
from backend.app.core.middleware import RequestContextMiddleware
from backend.app.domain.exceptions import DomainError
from backend.app.infrastructure.composition import build_container
from backend.app.infrastructure.runtime import RuntimeResources, build_runtime_resources
from backend.app.interfaces.api.exception_handlers import (
    handle_application_error,
    handle_domain_error,
    handle_http_error,
    handle_request_validation_error,
    handle_unexpected_error,
)
from backend.app.interfaces.api.v1.endpoints.dashboard import router as dashboard_router
from backend.app.interfaces.api.v1.endpoints.health import router as health_router
from backend.app.interfaces.api.v1.endpoints.incidents import router as incidents_router
from backend.app.interfaces.api.v1.endpoints.logs import router as logs_router
from backend.app.interfaces.api.v1.endpoints.rag import router as rag_router
from backend.app.interfaces.api.v1.endpoints.rca import router as rca_router
from backend.app.interfaces.api.v1.endpoints.reports import router as reports_router

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    container: ServiceContainer | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if container is not None:
            yield
            return
        resources: RuntimeResources = await build_runtime_resources(app_settings)
        application.state.container = resources.container
        try:
            yield
        finally:
            await resources.close()

    docs_enabled = app_settings.enable_api_docs
    application = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.container = (
        container if container is not None else build_container(app_settings)
    )

    if app_settings.allowed_hosts:
        application.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=app_settings.allowed_hosts,
        )
    if app_settings.cors_allowed_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=app_settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "If-Match",
                "Idempotency-Key",
                "X-Request-ID",
            ],
        )
    application.add_middleware(RequestContextMiddleware)

    application.add_exception_handler(ApplicationError, handle_application_error)
    application.add_exception_handler(DomainError, handle_domain_error)
    application.add_exception_handler(RequestValidationError, handle_request_validation_error)
    application.add_exception_handler(StarletteHTTPException, handle_http_error)
    application.add_exception_handler(Exception, handle_unexpected_error)

    application.include_router(health_router)
    application.include_router(logs_router, prefix=app_settings.api_v1_prefix)
    application.include_router(incidents_router, prefix=app_settings.api_v1_prefix)
    application.include_router(rag_router, prefix=app_settings.api_v1_prefix)
    application.include_router(rca_router, prefix=app_settings.api_v1_prefix)
    application.include_router(reports_router, prefix=app_settings.api_v1_prefix)
    application.include_router(dashboard_router, prefix=app_settings.api_v1_prefix)
    logger.info(
        "application_configured",
        extra={
            "event_fields": {
                "environment": app_settings.environment,
                "api_docs_enabled": docs_enabled,
            }
        },
    )
    return application


app = create_app()
