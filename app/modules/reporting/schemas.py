"""Draft transcription contract; deliberately separate from saved reports."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.report import LanguageHint


class TranscriptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    transcript: str = Field(min_length=1, max_length=5000)
    requested_language_hint: LanguageHint
    review_required: Literal[True] = True

    @field_validator("transcript")
    @classmethod
    def reject_blank_transcript(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Transcript must contain a non-whitespace character")
        return value
