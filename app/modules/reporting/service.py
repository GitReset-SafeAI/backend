"""Prepare English report text before the existing atomic creation operation."""

from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.modules.reporting.transcription.translation import TranslationError
from app.repositories.reports import ReportPersistenceError, ReportRepository
from app.schemas.report import LanguageHint, ReportCreateRequest, ReportResponse, ReportSubmission


class ReportTranslator(Protocol):
    async def translate(self, original_text: str, language_hint: LanguageHint) -> str: ...


class ReportService:
    def __init__(self, repository: ReportRepository, translator: ReportTranslator) -> None:
        self._repository = repository
        self._translator = translator

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
        return await self._repository.create(submission, reporter_id)
