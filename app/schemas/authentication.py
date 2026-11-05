"""Web registration and password-login contracts."""

import re

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class SignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=4096, repr=False)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class SignUpRequest(SignInRequest):
    name: str = Field(min_length=1, max_length=60)
    password: SecretStr = Field(min_length=8, max_length=4096, repr=False)
    nic: SecretStr = Field(min_length=10, max_length=12, repr=False)
    phone_number: str = Field(pattern=r"^\+94[1-9][0-9]{8}$", repr=False)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter your name.")
        return value

    @field_validator("nic")
    @classmethod
    def validate_nic(cls, value: SecretStr) -> SecretStr:
        # Format check only: never treat the number as verified identity.
        if not re.fullmatch(r"(?:[0-9]{9}[VvXx]|[0-9]{12})", value.get_secret_value()):
            raise ValueError("Enter a valid NIC format.")
        return value


class SignInResponse(BaseModel):
    access_token: SecretStr = Field(min_length=1, repr=False)
    refresh_token: SecretStr = Field(min_length=1, repr=False)
    token_type: str = Field(pattern=r"^bearer$")
    expires_in: int = Field(gt=0, strict=True)
