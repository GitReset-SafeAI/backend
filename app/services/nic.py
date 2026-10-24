"""Parse Sri Lankan NIC indicators locally, without checking identity or issuance."""

import re
from calendar import isleap

from app.schemas.nic import GenderIndicator, NICClassificationResponse, NICFormat

_OLD_NIC = re.compile(r"[0-9]{9}[VvXx]")
_NEW_NIC = re.compile(r"[0-9]{12}")
_FORMAT_ERROR = "Use 9 ASCII digits followed by V or X, or 12 ASCII digits."


class NICValidationError(ValueError):
    """A safe validation failure whose message never includes the submitted number."""


def classify_nic(nic: str) -> NICClassificationResponse:
    """Validate the structure/day code and return only its encoded indicator.

    NIC ordinals use a fixed 366-slot leap calendar, including in common years.
    Day 366 is therefore valid in every year; slot 060 denotes February 29 and
    needs a possible leap year. Legacy YY cannot resolve a century: YY=00 may
    mean 2000, so its leap-day slot is not rejected as if it necessarily meant
    1900. See docs/nic-classification.md for references and limitations.
    """
    if not isinstance(nic, str):
        raise NICValidationError(_FORMAT_ERROR)
    value = nic.strip()

    if _OLD_NIC.fullmatch(value):
        nic_format = NICFormat.OLD
        encoded_day = int(value[2:5])
        # All non-century YY leap patterns agree; 00 can be a leap century.
        possible_leap_year = int(value[:2]) % 4 == 0
    elif _NEW_NIC.fullmatch(value):
        nic_format = NICFormat.NEW
        year = int(value[:4])
        if year == 0:
            raise NICValidationError("NIC must encode a non-zero year.")
        encoded_day = int(value[4:7])
        possible_leap_year = isleap(year)
    else:
        raise NICValidationError(_FORMAT_ERROR)

    gender = GenderIndicator.FEMALE if encoded_day >= 500 else GenderIndicator.MALE
    day = encoded_day - 500 if gender == GenderIndicator.FEMALE else encoded_day
    if not 1 <= day <= 366:
        raise NICValidationError("NIC day code must be 001-366 or 501-866.")
    if day == 60 and not possible_leap_year:
        raise NICValidationError("NIC day code represents February 29 in a non-leap year.")

    return NICClassificationResponse(gender_indicator=gender, nic_format=nic_format)
