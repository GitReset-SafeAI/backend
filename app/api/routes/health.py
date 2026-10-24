"""Process health endpoint, independent of external services."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["saferoute-ai-backend"] = "saferoute-ai-backend"
    version: str


@router.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Report process health without probing database or AI services."""
    return HealthResponse(version=settings.app_version)
