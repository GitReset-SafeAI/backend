"""Settings loading and secret masking without local credentials or network access."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for name in (
        "APP_ENV",
        "APP_VERSION",
        "SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_JWKS_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "LLM_API_KEY",
        "GROQ_API_KEY",
        "TRANSCRIPTION_MODEL",
        "TRANSCRIPTION_TIMEOUT_SECONDS",
        "TRANSCRIPTION_MAX_BYTES",
        "TRANSCRIPTION_MAX_DURATION_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_without_credentials() -> None:
    settings = Settings()

    assert settings.app_env == "development"
    assert settings.app_version == "0.1.0"
    assert settings.supabase_url == ""
    assert settings.supabase_jwks_url == ""
    for field_name in (
        "supabase_publishable_key",
        "supabase_secret_key",
        "supabase_anon_key",
        "supabase_service_role_key",
        "llm_api_key",
        "groq_api_key",
    ):
        secret = getattr(settings, field_name)
        assert isinstance(secret, SecretStr)
        assert secret.get_secret_value() == ""


def test_modern_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "dummy-modern-publishable")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "dummy-modern-secret")
    monkeypatch.setenv("SUPABASE_JWKS_URL", "https://example.invalid/auth/v1/.well-known/jwks.json")

    settings = Settings()

    assert settings.supabase_url == "https://example.invalid"
    assert settings.supabase_jwks_url == "https://example.invalid/auth/v1/.well-known/jwks.json"
    assert settings.supabase_publishable_key.get_secret_value() == "dummy-modern-publishable"
    assert settings.supabase_secret_key.get_secret_value() == "dummy-modern-secret"
    assert settings.supabase_anon_key.get_secret_value() == ""
    assert settings.supabase_service_role_key.get_secret_value() == ""


def test_legacy_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_ANON_KEY", "dummy-legacy-anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "dummy-legacy-service-role")

    settings = Settings()

    assert settings.supabase_anon_key.get_secret_value() == "dummy-legacy-anon"
    assert settings.supabase_service_role_key.get_secret_value() == "dummy-legacy-service-role"
    assert settings.supabase_publishable_key.get_secret_value() == ""
    assert settings.supabase_secret_key.get_secret_value() == ""


def test_modern_and_legacy_keys_remain_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "dummy-modern-publishable")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "dummy-modern-secret")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "dummy-legacy-anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "dummy-legacy-service-role")

    settings = Settings()

    assert settings.supabase_publishable_key.get_secret_value() == "dummy-modern-publishable"
    assert settings.supabase_secret_key.get_secret_value() == "dummy-modern-secret"
    assert settings.supabase_anon_key.get_secret_value() == "dummy-legacy-anon"
    assert settings.supabase_service_role_key.get_secret_value() == "dummy-legacy-service-role"


def test_secret_representations_are_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_secrets = {
        "SUPABASE_PUBLISHABLE_KEY": "dummy-publishable-value",
        "SUPABASE_SECRET_KEY": "dummy-secret-value",
        "SUPABASE_ANON_KEY": "dummy-anon-value",
        "SUPABASE_SERVICE_ROLE_KEY": "dummy-service-role-value",
        "LLM_API_KEY": "dummy-llm-value",
        "GROQ_API_KEY": "dummy-groq-value",
    }
    for name, value in dummy_secrets.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_JWKS_URL", "https://example.invalid/jwks.json")

    settings = Settings()

    representations = (
        str(settings),
        repr(settings),
        repr(settings.model_dump()),
        settings.model_dump_json(),
    )
    for name, value in dummy_secrets.items():
        secret = getattr(settings, name.lower())
        assert isinstance(secret, SecretStr)
        assert secret.get_secret_value() == value
        assert str(secret) == "**********"
        for representation in representations:
            assert value not in representation
    assert settings.supabase_url in str(settings)
    assert settings.supabase_jwks_url in str(settings)


def test_utf8_dotenv_and_unknown_settings(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "APP_ENV=staging\n"
        "APP_VERSION=0.2.0\n"
        "SUPABASE_URL=https://example.invalid\n"
        "SUPABASE_PUBLISHABLE_KEY=dummy-dotenv-publishable\n"
        "SUPABASE_SECRET_KEY=dummy-dotenv-secret\n"
        "SUPABASE_JWKS_URL=https://example.invalid/jwks.json\n"
        "SUPABASE_ANON_KEY=dummy-dotenv-anon\n"
        "SUPABASE_SERVICE_ROLE_KEY=dummy-dotenv-service-role\n"
        "LLM_API_KEY=dummy-රහස\n"
        "UNRELATED_SETTING=ignored\n",
        encoding="utf-8",
    )

    settings = Settings()

    assert settings.app_env == "staging"
    assert settings.app_version == "0.2.0"
    assert settings.supabase_url == "https://example.invalid"
    assert settings.supabase_jwks_url == "https://example.invalid/jwks.json"
    assert settings.supabase_publishable_key.get_secret_value() == "dummy-dotenv-publishable"
    assert settings.supabase_secret_key.get_secret_value() == "dummy-dotenv-secret"
    assert settings.supabase_anon_key.get_secret_value() == "dummy-dotenv-anon"
    assert settings.supabase_service_role_key.get_secret_value() == "dummy-dotenv-service-role"
    assert settings.llm_api_key.get_secret_value() == "dummy-රහස"
    assert not hasattr(settings, "unrelated_setting")


def test_environment_overrides_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SUPABASE_SECRET_KEY=dummy-dotenv-secret\n", encoding="utf-8")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "dummy-environment-secret")

    settings = Settings()

    assert settings.supabase_secret_key.get_secret_value() == "dummy-environment-secret"


@pytest.mark.parametrize("app_env", ["development", "test", "staging", "production"])
def test_supported_app_environments(monkeypatch: pytest.MonkeyPatch, app_env: str) -> None:
    monkeypatch.setenv("APP_ENV", app_env)

    assert Settings().app_env == app_env


def test_invalid_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "unsupported")

    with pytest.raises(ValidationError):
        Settings()


def test_settings_cache_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "0.2.0")
    first = get_settings()
    monkeypatch.setenv("APP_VERSION", "0.3.0")

    assert get_settings() is first
    assert get_settings().app_version == "0.2.0"

    get_settings.cache_clear()
    refreshed = get_settings()

    assert refreshed is not first
    assert refreshed.app_version == "0.3.0"


def test_transcription_defaults_are_optional_and_separate() -> None:
    settings = Settings()
    assert settings.transcription_model == "whisper-large-v3"
    assert settings.transcription_timeout_seconds == 30
    assert settings.transcription_max_bytes == 10 * 1024 * 1024
    assert settings.transcription_max_duration_seconds == 60
    assert settings.groq_api_key.get_secret_value() == ""


def test_transcription_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "TRANSCRIPTION_MODEL": "dummy-speech-model",
        "TRANSCRIPTION_TIMEOUT_SECONDS": "12.5",
        "TRANSCRIPTION_MAX_BYTES": "1000000",
        "TRANSCRIPTION_MAX_DURATION_SECONDS": "15",
        "LLM_MODEL": "dummy-chat-model",
        "GROQ_API_KEY": "dummy-groq-key",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings()
    assert settings.transcription_model == "dummy-speech-model"
    assert settings.llm_model == "dummy-chat-model"
    assert settings.transcription_timeout_seconds == 12.5
    assert settings.transcription_max_bytes == 1000000
    assert settings.transcription_max_duration_seconds == 15


@pytest.mark.parametrize(
    "field,value",
    [
        ("transcription_model", ""),
        ("transcription_timeout_seconds", 0),
        ("transcription_timeout_seconds", -1),
        ("transcription_timeout_seconds", float("nan")),
        ("transcription_timeout_seconds", float("inf")),
        ("transcription_max_bytes", 0),
        ("transcription_max_bytes", -1),
        ("transcription_max_bytes", 10485761),
        ("transcription_max_duration_seconds", 0),
        ("transcription_max_duration_seconds", 60.1),
        ("transcription_max_duration_seconds", float("inf")),
    ],
)
def test_invalid_transcription_settings(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
