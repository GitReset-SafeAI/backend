"""Validation of shared report submissions and saved reports."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.report import (
    CrowdLevel,
    CurrentSafetyStatus,
    InputMethod,
    LanguageHint,
    LightingCondition,
    ReportResponse,
    ReportSubmission,
)


@pytest.fixture
def submission_data() -> dict[str, object]:
    text = "Synthetic demo: someone followed me after I left the bus."
    return {
        "original_text": text,
        "translated_text": text,
        "input_method": "text",
        "language_hint": "en",
    }


@pytest.fixture
def response_data(submission_data: dict[str, object]) -> dict[str, object]:
    return {
        **submission_data,
        "id": "4bf9b65c-9451-40e0-a40c-e26376a3a5d9",
        "submitted_at": "2026-10-23T04:30:00Z",
    }


def test_english_submission(submission_data: dict[str, object]) -> None:
    report = ReportSubmission.model_validate(submission_data)

    assert report.original_text == report.translated_text == submission_data["original_text"]
    assert report.input_method is InputMethod.TEXT
    assert report.language_hint is LanguageHint.ENGLISH
    assert report.latitude is None
    assert report.longitude is None
    assert report.occurred_at is None
    assert report.lighting_condition is None
    assert report.crowd_level is None
    assert report.current_safety_status is None
    assert set(report.model_dump()) == {
        "original_text",
        "translated_text",
        "input_method",
        "language_hint",
        "latitude",
        "longitude",
        "occurred_at",
        "lighting_condition",
        "crowd_level",
        "current_safety_status",
    }


def test_all_context_fields(
    submission_data: dict[str, object], response_data: dict[str, object]
) -> None:
    context = {
        "lighting_condition": "DAYLIGHT",
        "crowd_level": "NOT_VERY_CROWDED",
        "current_safety_status": "MAY_STILL_BE_IN_DANGER",
    }
    submission_data.update(context)
    response_data.update(context)

    for report in (
        ReportSubmission.model_validate(submission_data),
        ReportResponse.model_validate(response_data),
    ):
        assert report.lighting_condition is LightingCondition.DAYLIGHT
        assert report.crowd_level is CrowdLevel.NOT_VERY_CROWDED
        assert report.current_safety_status is CurrentSafetyStatus.MAY_STILL_BE_IN_DANGER
        for field_name, value in context.items():
            assert report.model_dump(mode="json")[field_name] == value


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("lighting_condition", "WELL_LIT"),
        ("lighting_condition", "PARTIALLY_LIT"),
        ("lighting_condition", "POORLY_LIT_OR_DARK"),
        ("lighting_condition", "DAYLIGHT"),
        ("lighting_condition", "UNKNOWN_OR_PREFER_NOT_TO_SAY"),
        ("crowd_level", "ALMOST_EMPTY"),
        ("crowd_level", "NOT_VERY_CROWDED"),
        ("crowd_level", "MODERATELY_CROWDED"),
        ("crowd_level", "VERY_CROWDED"),
        ("crowd_level", "UNKNOWN_OR_PREFER_NOT_TO_SAY"),
        ("current_safety_status", "SAFE_NOW"),
        ("current_safety_status", "UNSURE"),
        ("current_safety_status", "MAY_STILL_BE_IN_DANGER"),
        ("current_safety_status", "PREFER_NOT_TO_SAY"),
    ],
)
def test_allowed_context_values(
    submission_data: dict[str, object],
    response_data: dict[str, object],
    field_name: str,
    value: str,
) -> None:
    submission_data[field_name] = value
    response_data[field_name] = value

    for report in (
        ReportSubmission.model_validate(submission_data),
        ReportResponse.model_validate(response_data),
    ):
        assert getattr(report, field_name).value == value
        assert report.model_dump(mode="json")[field_name] == value


@pytest.mark.parametrize(
    "field_name", ["lighting_condition", "crowd_level", "current_safety_status"]
)
def test_context_field_accepts_null(
    submission_data: dict[str, object], response_data: dict[str, object], field_name: str
) -> None:
    submission_data[field_name] = None
    response_data[field_name] = None

    assert getattr(ReportSubmission.model_validate(submission_data), field_name) is None
    assert ReportResponse.model_validate(response_data).model_dump(mode="json")[field_name] is None


def test_all_context_fields_accept_null(
    submission_data: dict[str, object], response_data: dict[str, object]
) -> None:
    context = {"lighting_condition": None, "crowd_level": None, "current_safety_status": None}
    submission_data.update(context)
    response_data.update(context)

    for report in (
        ReportSubmission.model_validate(submission_data),
        ReportResponse.model_validate(response_data),
    ):
        assert report.lighting_condition is None
        assert report.crowd_level is None
        assert report.current_safety_status is None


@pytest.mark.parametrize(
    "field_name", ["lighting_condition", "crowd_level", "current_safety_status"]
)
@pytest.mark.parametrize("value", ["INVALID", "", 1, False])
def test_invalid_context_values(
    submission_data: dict[str, object],
    response_data: dict[str, object],
    field_name: str,
    value: object,
) -> None:
    submission_data[field_name] = value
    response_data[field_name] = value

    with pytest.raises(ValidationError) as error:
        ReportSubmission.model_validate(submission_data)
    assert error.value.errors()[0]["loc"] == (field_name,)
    with pytest.raises(ValidationError):
        ReportResponse.model_validate(response_data)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("lighting_condition", "well_lit"),
        ("lighting_condition", "SAFE_NOW"),
        ("crowd_level", "very_crowded"),
        ("crowd_level", "DAYLIGHT"),
        ("current_safety_status", "safe_now"),
        ("current_safety_status", "WELL_LIT"),
    ],
)
def test_context_enums_are_case_sensitive_and_independent(
    submission_data: dict[str, object], field_name: str, value: str
) -> None:
    submission_data[field_name] = value

    with pytest.raises(ValidationError):
        ReportSubmission.model_validate(submission_data)


@pytest.mark.parametrize(
    ("original_text", "language_hint"),
    [
        ("කෙනෙක් බස් එකෙන් බැස්සට පස්සේ මගේ පස්සෙන් ආවා.", "si"),
        ("Bus eken bassaata passe kenek mage passen aawa.", "si-Latn"),
        ("பேருந்திலிருந்து இறங்கிய பின் ஒருவர் என்னைப் பின்தொடர்ந்தார்.", "ta"),
        ("Bus eken bassaata passe someone followed me.", "mixed"),
        ("Bus eken bassaata passe kenek mage passen aawa.", "unknown"),
    ],
)
def test_multilingual_submission(
    submission_data: dict[str, object], original_text: str, language_hint: str
) -> None:
    submission_data.update(original_text=original_text, language_hint=language_hint)

    report = ReportSubmission.model_validate(submission_data)

    assert report.original_text == original_text
    assert report.translated_text == submission_data["translated_text"]
    assert report.language_hint.value == language_hint


def test_voice_input_method(submission_data: dict[str, object]) -> None:
    submission_data["input_method"] = "voice"

    assert ReportSubmission.model_validate(submission_data).input_method is InputMethod.VOICE


@pytest.mark.parametrize(
    "field_name", ["original_text", "translated_text", "input_method", "language_hint"]
)
def test_required_submission_fields(submission_data: dict[str, object], field_name: str) -> None:
    del submission_data[field_name]

    with pytest.raises(ValidationError) as error:
        ReportSubmission.model_validate(submission_data)

    assert error.value.errors()[0]["loc"] == (field_name,)


@pytest.mark.parametrize("field_name", ["original_text", "translated_text"])
@pytest.mark.parametrize("value", [None, "", " ", "\t\n\r", "\u2003"])
def test_null_empty_and_blank_text(
    submission_data: dict[str, object], field_name: str, value: str | None
) -> None:
    submission_data[field_name] = value

    with pytest.raises(ValidationError):
        ReportSubmission.model_validate(submission_data)


@pytest.mark.parametrize("field_name", ["original_text", "translated_text"])
@pytest.mark.parametrize("length", [1, 5000])
def test_text_length_boundaries(
    submission_data: dict[str, object], field_name: str, length: int
) -> None:
    submission_data[field_name] = "a" * length

    report = ReportSubmission.model_validate(submission_data)

    assert len(getattr(report, field_name)) == length


@pytest.mark.parametrize("field_name", ["original_text", "translated_text"])
def test_oversized_text(submission_data: dict[str, object], field_name: str) -> None:
    submission_data[field_name] = "a" * 5001

    with pytest.raises(ValidationError):
        ReportSubmission.model_validate(submission_data)


def test_valid_text_is_preserved(submission_data: dict[str, object]) -> None:
    submission_data.update(original_text="  Original text.\n", translated_text="  Translation.\n")

    report = ReportSubmission.model_validate(submission_data)

    assert report.original_text == submission_data["original_text"]
    assert report.translated_text == submission_data["translated_text"]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("input_method", "audio"),
        ("input_method", "TEXT"),
        ("language_hint", "fr"),
        ("language_hint", "SI"),
    ],
)
def test_invalid_enums(submission_data: dict[str, object], field_name: str, value: str) -> None:
    submission_data[field_name] = value

    with pytest.raises(ValidationError):
        ReportSubmission.model_validate(submission_data)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(None, None), (-90, -180), (90, 180), (0, 0)],
)
def test_valid_coordinate_pairs(
    submission_data: dict[str, object], latitude: float | None, longitude: float | None
) -> None:
    submission_data.update(latitude=latitude, longitude=longitude)

    report = ReportSubmission.model_validate(submission_data)

    assert report.latitude == latitude
    assert report.longitude == longitude


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (-90.1, 0),
        (90.1, 0),
        (0, -180.1),
        (0, 180.1),
        (float("nan"), 0),
        (0, float("nan")),
        (float("inf"), 0),
        (0, float("-inf")),
    ],
)
def test_invalid_coordinates(
    submission_data: dict[str, object], latitude: float, longitude: float
) -> None:
    submission_data.update(latitude=latitude, longitude=longitude)

    with pytest.raises(ValidationError):
        ReportSubmission.model_validate(submission_data)


@pytest.mark.parametrize(
    "coordinates",
    [
        {"latitude": 0},
        {"longitude": 0},
        {"latitude": 0, "longitude": None},
        {"latitude": None, "longitude": 0},
    ],
)
def test_unpaired_coordinates(
    submission_data: dict[str, object], coordinates: dict[str, float | None]
) -> None:
    submission_data.update(coordinates)

    with pytest.raises(ValidationError, match="Latitude and longitude must be provided together"):
        ReportSubmission.model_validate(submission_data)


@pytest.mark.parametrize("occurred_at", [datetime(2026, 10, 23, 10), "2026-10-23T10:00:00"])
def test_naive_occurrence_time(
    submission_data: dict[str, object], occurred_at: datetime | str
) -> None:
    submission_data["occurred_at"] = occurred_at

    with pytest.raises(ValidationError):
        ReportSubmission.model_validate(submission_data)


def test_aware_occurrence_time(submission_data: dict[str, object]) -> None:
    submission_data["occurred_at"] = "2026-10-23T10:00:00+05:30"

    report = ReportSubmission.model_validate(submission_data)

    assert report.occurred_at is not None
    assert report.occurred_at.utcoffset() == timedelta(hours=5, minutes=30)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("id", "4bf9b65c-9451-40e0-a40c-e26376a3a5d9"),
        ("submitted_at", "2026-10-23T04:30:00Z"),
        ("is_synthetic", True),
    ],
)
def test_submission_rejects_server_and_unapproved_fields(
    submission_data: dict[str, object], field_name: str, value: object
) -> None:
    submission_data[field_name] = value

    with pytest.raises(ValidationError):
        ReportSubmission.model_validate(submission_data)


def test_saved_report(response_data: dict[str, object]) -> None:
    report = ReportResponse.model_validate(response_data)

    assert isinstance(report.id, UUID)
    assert str(report.id) == response_data["id"]
    assert report.submitted_at == datetime(2026, 10, 23, 4, 30, tzinfo=UTC)
    assert report.submitted_at.utcoffset() == timedelta(0)
    assert report.lighting_condition is None
    assert report.crowd_level is None
    assert report.current_safety_status is None
    assert set(report.model_dump()) == set(ReportSubmission.model_fields) | {"id", "submitted_at"}
    assert report.model_dump(mode="json")["submitted_at"] == "2026-10-23T04:30:00Z"


@pytest.mark.parametrize("field_name", ["id", "submitted_at"])
def test_saved_report_requires_server_fields(
    response_data: dict[str, object], field_name: str
) -> None:
    del response_data[field_name]

    with pytest.raises(ValidationError):
        ReportResponse.model_validate(response_data)


def test_saved_report_rejects_invalid_uuid(response_data: dict[str, object]) -> None:
    response_data["id"] = "invalid-uuid"

    with pytest.raises(ValidationError):
        ReportResponse.model_validate(response_data)


@pytest.mark.parametrize("submitted_at", [datetime(2026, 10, 23, 4, 30), "2026-10-23T04:30:00"])
def test_saved_report_rejects_naive_submission_time(
    response_data: dict[str, object], submitted_at: datetime | str
) -> None:
    response_data["submitted_at"] = submitted_at

    with pytest.raises(ValidationError):
        ReportResponse.model_validate(response_data)


def test_saved_report_normalises_submission_time_to_utc(response_data: dict[str, object]) -> None:
    response_data["submitted_at"] = "2026-10-23T10:00:00+05:30"

    report = ReportResponse.model_validate(response_data)

    assert report.submitted_at == datetime(2026, 10, 23, 4, 30, tzinfo=UTC)
    assert report.submitted_at.tzinfo is UTC
