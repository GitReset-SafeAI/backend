"""Request and response contracts for provisional incident analysis."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnalyzeIncidentRequest(BaseModel):
    """English report text prepared by the reporting module."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    translated_text: str = Field(min_length=1, max_length=5000)


class ExtractedDetails(BaseModel):
    """Details explicitly stated in the report; unknown values remain null."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None
    reported_behaviours: list[str]
    reported_location: str | None
    reported_time: str | None
    immediate_danger_reported: bool | None


class IncidentAnalysis(BaseModel):
    """A provisional AI interpretation, not proof that an incident occurred."""

    model_config = ConfigDict(extra="forbid")

    analysis_status: Literal["completed", "needs_review"]
    category: Literal[
        "unwanted_touching",
        "sexual_remarks",
        "stalking",
        "threatening_behaviour",
        "other",
        "unclear",
    ]
    severity: Literal["low", "moderate", "high", "critical", "undetermined"]
    confidence: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    extracted_details: ExtractedDetails
    review_reason: str | None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.analysis_status == "completed":
            valid = (
                self.category != "unclear"
                and self.severity != "undetermined"
                and self.confidence is not None
                and self.review_reason is None
            )
        else:
            valid = (
                self.category == "unclear"
                and self.severity == "undetermined"
                and self.review_reason is not None
                and bool(self.review_reason.strip())
            )
        if not valid:
            raise ValueError("Analysis fields are inconsistent with analysis_status")
        return self
