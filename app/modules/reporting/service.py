"""Prepare English report text before the existing atomic creation operation."""

import logging
from datetime import timedelta, timezone
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.modules.incident_ai.repository import AnalysisStorageError
from app.modules.incident_ai.service import IncidentAnalysisService
from app.modules.incident_ai.time_periods import normalize_reported_time
from app.modules.reporting.transcription.translation import TranslationError
from app.repositories.reports import ReportPersistenceError, ReportRepository
from app.schemas.report import LanguageHint, ReportCreateRequest, ReportResponse, ReportSubmission


class ReportTranslator(Protocol):
    async def translate(self, original_text: str, language_hint: LanguageHint) -> str: ...


class ReportService:
    def __init__(
        self,
        repository: ReportRepository,
        translator: ReportTranslator,
        analyzer: IncidentAnalysisService | None = None,
    ) -> None:
        self._repository = repository
        self._translator = translator
        self._analyzer = analyzer

    async def create(self, request: ReportCreateRequest, reporter_id: UUID) -> ReportResponse:
        """Never insert until English preparation has succeeded."""
        if not isinstance(reporter_id, UUID):
            raise ReportPersistenceError
        if request.language_hint is LanguageHint.ENGLISH:
            translated_text = request.original_text
        elif request.translated_text is not None:
            # Compatibility only: this is not an independently verified translation.
            translated_text = request.translated_text
        else:
            translated_text = await self._translator.translate(
                request.original_text, request.language_hint
            )
        try:
            submission = ReportSubmission.model_validate(
                {**request.model_dump(), "translated_text": translated_text}
            )
        except ValidationError:
            raise TranslationError from None
        saved = await self._repository.create(submission, reporter_id)
        if self._analyzer is not None:
            lighting = {
                "WELL_LIT": "well_lit",
                "PARTIALLY_LIT": "partially_lit",
                "POORLY_LIT_OR_DARK": "poorly_lit_or_dark",
                "DAYLIGHT": "day_light",
                "UNKNOWN_OR_PREFER_NOT_TO_SAY": "not_sure",
            }
            reported_time = None
            if saved.reported_time is not None:
                reported_time = normalize_reported_time(saved.reported_time)
            elif saved.occurred_at is not None:
                # Interpret the incident timestamp in the application's Sri Lankan locale.
                local_time = saved.occurred_at.astimezone(timezone(timedelta(hours=5, minutes=30)))
                hour = local_time.hour
                reported_time = (
                    "late_night"
                    if hour < 5
                    else "morning"
                    if hour < 12
                    else "afternoon"
                    if hour < 17
                    else "evening"
                    if hour < 21
                    else "night"
                )
            try:
                await self._analyzer.analyze(
                    saved.translated_text,
                    saved.id,
                    reported_time,
                    lighting.get(saved.lighting_condition),
                )
            except AnalysisStorageError:
                # The report has already committed. Do not invite duplicate submissions
                # by claiming creation failed, and never log private report content.
                logging.getLogger(__name__).warning(
                    "Automatic incident analysis could not be saved"
                )
        return saved
