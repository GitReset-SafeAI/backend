"""Local NIC parsing with invented serial/check digits, never real NIC records."""

import pytest
from pydantic import ValidationError

from app.schemas.nic import NICClassificationRequest, NICClassificationResponse
from app.services.nic import NICValidationError, classify_nic


def synthetic_nic(day: int, nic_format: str, year: int = 1991) -> str:
    """Construct a test value; its serial/check digits are not issuance-validated."""
    if nic_format == "old":
        return f"{year % 100:02d}{day:03d}0000V"
    return f"{year:04d}{day:03d}00000"


@pytest.mark.parametrize("nic_format", ["old", "new"])
@pytest.mark.parametrize(
    "day,gender",
    [
        (1, "male"),
        (59, "male"),
        (61, "male"),
        (365, "male"),
        (366, "male"),
        (501, "female"),
        (559, "female"),
        (561, "female"),
        (865, "female"),
        (866, "female"),
    ],
)
def test_valid_indicators_and_fixed_leap_calendar(day: int, gender: str, nic_format: str) -> None:
    nic = synthetic_nic(day, nic_format)

    result = classify_nic(nic)

    assert result.model_dump(mode="json") == {
        "gender_indicator": gender,
        "nic_format": nic_format,
        "classification_method": "nic_format",
        "identity_verified": False,
    }
    assert nic not in result.model_dump_json()


@pytest.mark.parametrize("suffix", ["V", "v", "X", "x"])
@pytest.mark.parametrize("padding", ["", " \t\n", "\u2003"])
def test_legacy_suffix_and_surrounding_whitespace(suffix: str, padding: str) -> None:
    result = classify_nic(f"{padding}915010000{suffix}{padding}")

    assert result.gender_indicator == "female"
    assert result.nic_format == "old"


def test_new_format_surrounding_whitespace() -> None:
    assert classify_nic(" \t199100100000\n").gender_indicator == "male"


@pytest.mark.parametrize(
    "nic",
    [
        "",
        " \t\n",
        "91001000V",
        "9100100000V",
        "910010000",
        "910010000Z",
        "910010000VV",
        "19910010000",
        "1991001000000",
        "199100100000V",
        "19910A100000",
        "91A010000V",
        "1991 00100000",
        "1991-00100000",
        "\u066199100100000",
        "\uff119100100000",
    ],
)
def test_malformed_structure(nic: str) -> None:
    with pytest.raises(NICValidationError, match="ASCII digits"):
        classify_nic(nic)


@pytest.mark.parametrize("nic_format", ["old", "new"])
@pytest.mark.parametrize("day", [0, 367, 499, 500, 867, 999])
def test_impossible_day_ranges(nic_format: str, day: int) -> None:
    with pytest.raises(NICValidationError, match="day code must be"):
        classify_nic(synthetic_nic(day, nic_format))


@pytest.mark.parametrize("nic_format", ["old", "new"])
@pytest.mark.parametrize("day", [60, 560])
@pytest.mark.parametrize("year", [1992, 1996, 2000, 2004])
def test_february_29_in_possible_leap_years(nic_format: str, day: int, year: int) -> None:
    assert classify_nic(synthetic_nic(day, nic_format, year)).nic_format == nic_format


@pytest.mark.parametrize("nic_format", ["old", "new"])
@pytest.mark.parametrize("day", [60, 560])
@pytest.mark.parametrize("year", [1991, 1999, 2001])
def test_reserved_february_29_slot_in_common_years(nic_format: str, day: int, year: int) -> None:
    with pytest.raises(NICValidationError, match="February 29"):
        classify_nic(synthetic_nic(day, nic_format, year))


@pytest.mark.parametrize("day", [60, 560])
@pytest.mark.parametrize("year", [1900, 2100, 2200])
def test_new_format_observes_century_leap_year_rules(day: int, year: int) -> None:
    with pytest.raises(NICValidationError, match="February 29"):
        classify_nic(synthetic_nic(day, "new", year))


@pytest.mark.parametrize("day", [60, 560])
def test_legacy_00_does_not_invent_a_century(day: int) -> None:
    # YY=00 cannot distinguish non-leap 1900 from leap 2000.
    assert classify_nic(synthetic_nic(day, "old", 2000)).nic_format == "old"


def test_new_format_rejects_year_zero_without_adding_age_rules() -> None:
    with pytest.raises(NICValidationError, match="non-zero year"):
        classify_nic("000000100000")
    assert classify_nic(synthetic_nic(366, "new", 9999)).nic_format == "new"


@pytest.mark.parametrize("value", [None, 199100100000, True, [], {}])
def test_request_requires_a_string(value: object) -> None:
    with pytest.raises(ValidationError):
        NICClassificationRequest(nic=value)


def test_request_masks_normal_representations() -> None:
    nic = synthetic_nic(501, "new")
    request = NICClassificationRequest(nic=nic)

    assert request.nic.get_secret_value() == nic
    for representation in (repr(request), str(request), request.model_dump_json()):
        assert nic not in representation


def test_parser_error_does_not_echo_input() -> None:
    nic = "199199900000"
    with pytest.raises(NICValidationError) as error:
        classify_nic(nic)

    assert nic not in str(error.value)
    assert nic not in repr(error.value)


def test_result_cannot_claim_identity_verification() -> None:
    with pytest.raises(ValidationError):
        NICClassificationResponse(
            gender_indicator="female", nic_format="new", identity_verified=True
        )
