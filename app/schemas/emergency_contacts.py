"""Private emergency-contact input and response contracts."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContactInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=60)
    phone: str = Field(pattern=r"^\+94[1-9][0-9]{8}$", repr=False)
    relationship: Literal["family", "friend", "partner", "other"] | None = None

    @field_validator("name")
    @classmethod
    def trimmed_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a contact name.")
        return value


class EmergencyContact(ContactInput):
    id: UUID
    slot: int = Field(ge=0, le=4, strict=True)
