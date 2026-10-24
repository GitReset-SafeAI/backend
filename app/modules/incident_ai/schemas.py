"""Database-aligned analysis contracts."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

StreetLights = Literal["well_lit", "partially_lit", "poorly_lit_or_dark", "day_light", "not_sure"]


class AnalyzeIncidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    report_id: UUID
    translated_text: str = Field(min_length=1, max_length=5000)
    reported_time: str | None = Field(default=None, min_length=1, max_length=500)
    street_lights: StreetLights | None = None


class ExtractedDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str | None
    reported_behaviours: list[str]
    reported_location: str | None


class AnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_status: Literal["completed", "needs_review"]
    IncidentCategory: (
        Literal[
            "UNWANTED_FOLLOWING",
            "VERBAL_SEXUAL_HARASSMENT",
            "UNWANTED_PHYSICAL_CONTACT",
            "THREATENING_BEHAVIOUR",
            "UNCOMFORTABLE_STARING",
            "OTHER",
            "UNCLEAR",
        ]
        | None
    )
    SafetyConcernCategory: (
        Literal[
            "POOR_STREET_LIGHTING",
            "ISOLATED_OR_DESERTED_ROAD",
            "UNSAFE_BUS_STOP",
            "FEELING_UNSAFE_WITHOUT_CONFIRMED_INCIDENT",
            "PREVIOUS_SAFETY_CONCERNS_AT_LOCATION",
            "OTHER",
            "UNCLEAR",
        ]
        | None
    )
    reported_time: str | None
    street_lights: StreetLights
    immediate_danger_reported: bool | None
    severity: int | None = Field(ge=0, le=10, strict=True)
    confidence: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    extracted_details: ExtractedDetails


class IncidentAnalysis(AnalysisOutput):
    id: UUID = Field(default_factory=uuid4)
    report_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
