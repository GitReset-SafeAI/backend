"""Environment-based application settings."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Optional integration credentials permit standalone local startup."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_version: str = "0.1.0"
    supabase_url: str = ""
    supabase_anon_key: SecretStr = SecretStr("")
    supabase_service_role_key: SecretStr = SecretStr("")
    groq_api_key: SecretStr = SecretStr("")
    llm_model: str = "openai/gpt-oss-20b"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_timeout_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
