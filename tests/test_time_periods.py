"""Time boundaries and ambiguity must not depend on the LLM."""

import pytest

from app.modules.incident_ai.schemas import AnalyzeIncidentRequest
from app.modules.incident_ai.time_periods import normalize_reported_time


@pytest.mark.parametrize(
    "value,expected",
    [
        ("00:00", "late_night"),
        ("04:59", "late_night"),
        ("05:00", "morning"),
        ("11:59 AM", "morning"),
        ("12 PM", "afternoon"),
        ("16:59", "afternoon"),
        ("17:00", "evening"),
        ("20:59", "evening"),
        ("21:00", "night"),
        ("23:59", "night"),
        ("12 AM", "late_night"),
        ("5.00", "not_sure"),
        ("5.00 pm", "evening"),
        ("5 AM", "morning"),
    ],
)
def test_time_boundaries(value: str, expected: str) -> None:
    assert normalize_reported_time(value) == expected
    request = AnalyzeIncidentRequest(
        report_id="00000000-0000-0000-0000-000000000001",
        translated_text="A report",
        reported_time=value,
    )
    assert request.reported_time == expected


@pytest.mark.parametrize("value", ["24:00", "17:60", "0 PM", "13 PM"])
def test_invalid_times(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_reported_time(value)
