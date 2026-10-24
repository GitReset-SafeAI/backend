"""Environment settings for the incident-understanding module."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class IncidentUnderstandingSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INCIDENT_UNDERSTANDING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = False
    api_key: SecretStr = SecretStr("")
    model: str = Field(default="gpt-4.1-mini", min_length=1)
    timeout_seconds: float = Field(default=30, gt=0)
    max_retries: int = Field(default=2, ge=0)
    max_report_characters: int = Field(default=10000, gt=0)
    max_output_tokens: int = Field(default=1500, gt=0)
    confidence_threshold: float = Field(default=0.65, ge=0, le=1)


@lru_cache
def get_incident_understanding_settings() -> IncidentUnderstandingSettings:
    return IncidentUnderstandingSettings()
