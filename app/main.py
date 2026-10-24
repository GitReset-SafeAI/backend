"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.routes.health import router as health_router
from app.api.routes.reports import router as reports_router
from app.core.config import get_settings
from app.core.errors import validation_error_handler


def create_app() -> FastAPI:
    """Create the application and register versioned API routers."""
    settings = get_settings()
    development = settings.app_env == "development"
    application = FastAPI(
        title="SafeRoute AI Backend",
        description="Backend foundation for harassment reporting and travel safety in Sri Lanka.",
        version=settings.app_version,
        docs_url="/docs" if development else None,
        redoc_url="/redoc" if development else None,
        openapi_url="/openapi.json" if development else None,
    )
    application.include_router(health_router, prefix="/api/v1")
    application.include_router(reports_router, prefix="/api/v1")
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    return application


app = create_app()
