"""Shared report submission and saved-report schemas."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema


class InputMethod(StrEnum):
    """Supported report input methods."""

    TEXT = "text"
    VOICE = "voice"


class LanguageHint(StrEnum):
    """User-supplied language hints, including Singlish and mixed input."""

    SINHALA = "si"
    SINHALA_LATIN = "si-Latn"
    ENGLISH = "en"
    TAMIL = "ta"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class LightingCondition(StrEnum):
    """Optional, self-reported lighting conditions."""

    WELL_LIT = "WELL_LIT"
    PARTIALLY_LIT = "PARTIALLY_LIT"
    POORLY_LIT_OR_DARK = "POORLY_LIT_OR_DARK"
    DAYLIGHT = "DAYLIGHT"
    UNKNOWN_OR_PREFER_NOT_TO_SAY = "UNKNOWN_OR_PREFER_NOT_TO_SAY"


class CrowdLevel(StrEnum):
    """Optional, self-reported crowd levels."""

    ALMOST_EMPTY = "ALMOST_EMPTY"
    NOT_VERY_CROWDED = "NOT_VERY_CROWDED"
    MODERATELY_CROWDED = "MODERATELY_CROWDED"
    VERY_CROWDED = "VERY_CROWDED"
    UNKNOWN_OR_PREFER_NOT_TO_SAY = "UNKNOWN_OR_PREFER_NOT_TO_SAY"


class CurrentSafetyStatus(StrEnum):
    """User-reported safety when reporting; independent of AI assessments."""

    SAFE_NOW = "SAFE_NOW"
    UNSURE = "UNSURE"
    MAY_STILL_BE_IN_DANGER = "MAY_STILL_BE_IN_DANGER"
    PREFER_NOT_TO_SAY = "PREFER_NOT_TO_SAY"


class ReportSubmission(BaseModel):
    """Client fields, including translated text supplied by the reporting workflow."""

    model_config = ConfigDict(extra="forbid")

    original_text: str = Field(min_length=1, max_length=5000)
    translated_text: str = Field(min_length=1, max_length=5000)
    input_method: InputMethod
    language_hint: LanguageHint
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    occurred_at: AwareDatetime | None = None
    lighting_condition: LightingCondition | None = Field(
        default=None, description="Optional, self-reported incident lighting conditions."
    )
    crowd_level: CrowdLevel | None = Field(
        default=None, description="Optional, self-reported incident crowd level."
    )
    current_safety_status: CurrentSafetyStatus | None = Field(
        default=None,
        description="Optional safety status when reporting, supplied by the user. "
        "This does not contact emergency services.",
    )

    @field_validator("original_text", "translated_text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """Reject whitespace-only input without changing the submitted text."""
        if not value.strip():
            raise ValueError("Text must contain a non-whitespace character")
        return value

    @model_validator(mode="after")
    def validate_coordinate_pair(self) -> Self:
        """Coordinates must both have values or both be absent."""
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Latitude and longitude must be provided together")
        return self


class ReportCreateRequest(ReportSubmission):
    """Creation input; omission requests server-side English preparation."""

    translated_text: str | SkipJsonSchema[None] = Field(
        default=None,
        min_length=1,
        max_length=5000,
        description="Optional legacy translation. Omit to request English preparation; "
        "null is invalid.",
    )

    @field_validator("translated_text", mode="before")
    @classmethod
    def reject_explicit_null_translation(cls, value: object) -> object:
        """Keep null invalid while allowing the field to be absent entirely."""
        if value is None:
            raise ValueError("Omit translated_text instead of supplying null")
        return value


class ReportResponse(ReportSubmission):
    """Saved report with an ID and submission time explicitly supplied by server code."""

    id: UUID
    submitted_at: AwareDatetime

    @field_validator("submitted_at")
    @classmethod
    def normalise_submission_time(cls, value: datetime) -> datetime:
        """Represent an aware submission timestamp in UTC."""
        return value.astimezone(UTC)
