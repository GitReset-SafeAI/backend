"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router as api_router
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
    # The web client runs on its own dev server, so the browser needs the
    # backend to say it may be called. Development only, and only the local
    # dev origins: a deployed frontend is served its own origin or an
    # explicitly configured one, never a wildcard.
    if development and settings.cors_allow_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization"],
        )

    # One registration point, so adding a feature router cannot leave it
    # mounted here and missing there. app/api/router.py holds the list.
    application.include_router(api_router, prefix="/api/v1")
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    return application


app = create_app()
