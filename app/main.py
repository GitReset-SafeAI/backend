"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.router import router as api_router
from app.core.config import get_settings


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
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
