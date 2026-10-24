"""Automatic analysis runs only after report persistence and preserves saved reports."""

import asyncio
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.modules.incident_ai.repository import AnalysisStorageError
from app.modules.reporting.service import ReportService
from app.repositories.reports import ReportPersistenceError
from app.schemas.report import ReportCreateRequest, ReportResponse

OWNER = UUID("11111111-1111-4111-8111-111111111111")
REPORT = UUID("22222222-2222-4222-8222-222222222222")


@pytest.mark.parametrize("unavailable", [False, True])
def test_saved_translation_and_context_used(unavailable: bool, caplog) -> None:
    request = ReportCreateRequest(
        original_text="Synthetic report",
        language_hint="en",
        input_method="text",
        lighting_condition="PARTIALLY_LIT",
        occurred_at="2026-10-24T12:00:00Z",
    )
    saved = ReportResponse(
        **(request.model_dump() | {"translated_text": "Saved English report"}),
        id=REPORT,
        submitted_at="2026-10-24T13:00:00Z",
    )
    repository = AsyncMock()
    repository.create.return_value = saved
    analyzer = AsyncMock()
    if unavailable:
        analyzer.analyze.side_effect = AnalysisStorageError("private database response")

    async def run():
        service = ReportService(repository, AsyncMock(), analyzer)
        result = await service.create(request, OWNER)
        assert result == saved
        repository.create.assert_awaited_once()
        analyzer.analyze.assert_awaited_once_with(
            "Saved English report", REPORT, "evening", "partially_lit"
        )

    asyncio.run(run())
    assert "private database response" not in caplog.text
    assert "Saved English report" not in caplog.text


def test_failed_report_insert_does_not_start_analysis() -> None:
    repository = AsyncMock()
    repository.create.side_effect = ReportPersistenceError()
    analyzer = AsyncMock()
    service = ReportService(repository, AsyncMock(), analyzer)
    request = ReportCreateRequest(
        original_text="Synthetic report", language_hint="en", input_method="text"
    )
    with pytest.raises(ReportPersistenceError):
        asyncio.run(service.create(request, OWNER))
    analyzer.analyze.assert_not_awaited()
