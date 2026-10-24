"""Transient NIC classification contracts; no identity or eligibility verification."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class GenderIndicator(StrEnum):
    """The binary indicator encoded in the number, not an account gender."""

    MALE = "male"
    FEMALE = "female"


class NICFormat(StrEnum):
    OLD = "old"
    NEW = "new"


class NICClassificationRequest(BaseModel):
    """Accept the number only in the request body and mask normal representations."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    nic: SecretStr = Field(repr=False, description="NIC to parse in memory; never persisted.")


class NICClassificationResponse(BaseModel):
    """Only a format-based result; deliberately excludes the NIC and birth date."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gender_indicator: GenderIndicator
    nic_format: NICFormat
    classification_method: Literal["nic_format"] = "nic_format"
    identity_verified: Literal[False] = False
