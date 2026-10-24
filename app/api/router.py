"""Central registration of health and feature routers."""

from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.reports import router as reports_router
from app.modules.incident_ai.router import router as incident_ai_router
from app.modules.integrity.router import router as integrity_router
from app.modules.reporting.router import router as reporting_router
from app.modules.safety_map.router import router as safety_map_router

router = APIRouter()
router.include_router(health_router)
router.include_router(reports_router)
router.include_router(reporting_router)
router.include_router(incident_ai_router)
router.include_router(integrity_router)
router.include_router(safety_map_router)
