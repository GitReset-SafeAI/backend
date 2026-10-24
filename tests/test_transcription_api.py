"""Authenticated raw-audio endpoint; provider HTTP is mocked and other workflows forbidden."""

import asyncio
import struct
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.core.config import Settings, get_settings
from app.core.security import AuthenticatedUser
from app.modules.reporting.transcription.service import SUPPORTED_MEDIA_TYPES

ENDPOINT = "/api/v1/reporting/transcriptions"


class API:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.body: object = {"text": "  Synthetic private draft: I am not sure.\n"}
        self.status = 200
        self.failure: Exception | None = None
        self.raw: bytes | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert str(request.url) == "https://provider.invalid/openai/v1/audio/transcriptions"
        if self.failure:
            raise self.failure
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw)
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        supabase_url="https://example.invalid",
        supabase_jwks_url="https://example.invalid/auth/v1/.well-known/jwks.json",
        groq_api_key="dummy-groq-key",
        llm_base_url="https://provider.invalid/openai/v1",
    )


@pytest.fixture
def api() -> API:
    return API()


@pytest.fixture
def application(monkeypatch: pytest.MonkeyPatch, tmp_path, settings: Settings, api: API):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        UUID("11111111-1111-4111-8111-111111111111")
    )
    original = httpx.AsyncClient

    def provider_client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(api))

    monkeypatch.setattr(
        "app.modules.reporting.transcription.provider.httpx.AsyncClient", provider_client
    )

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Transcription must never touch reports, translation, or classification"
        )

    for path in (
        "app.repositories.reports.ReportRepository.create",
        "app.repositories.reports.ReportRepository.get_owned_translation",
        "app.modules.reporting.transcription.translation.GroqReportTranslator.translate",
        "app.modules.incident_ai.classifier.GroqIncidentClassifier.classify",
        "app.modules.incident_ai.repository.SupabaseAnalysisRepository.save",
    ):
        monkeypatch.setattr(path, forbidden)
    yield app
    get_settings.cache_clear()


@pytest.mark.parametrize("hint", ["en", "si", "ta", "si-Latn", "mixed", "unknown"])
def test_success_returns_draft_without_other_workflows(
    application: FastAPI,
    api: API,
    wav_audio: bytes,
    hint: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("DEBUG"), TestClient(application) as client:
        response = client.post(
            ENDPOINT,
            params={"language_hint": hint},
            content=wav_audio,
            headers={"Content-Type": "audio/wav"},
        )
    assert response.status_code == 200
    assert response.json() == {
        "transcript": "  Synthetic private draft: I am not sure.\n",
        "requested_language_hint": hint,
        "review_required": True,
    }
    assert len(api.requests) == 1
    assert settings_has_no_database_credentials(application)
    for value in ("dummy-groq-key", api.body["text"], repr(wav_audio)):
        assert value not in caplog.text


def settings_has_no_database_credentials(application: FastAPI) -> bool:
    settings = application.dependency_overrides[get_settings]()
    return (
        not settings.supabase_secret_key.get_secret_value()
        and not settings.supabase_service_role_key.get_secret_value()
    )


def test_default_hint_is_unknown(application: FastAPI, wav_audio: bytes) -> None:
    with TestClient(application) as client:
        response = client.post(ENDPOINT, content=wav_audio, headers={"Content-Type": "audio/wav"})
    assert response.status_code == 200
    assert response.json()["requested_language_hint"] == "unknown"


@pytest.mark.parametrize(
    "kind, status",
    [
        ("empty", 422),
        ("silent", 422),
        ("truncated", 422),
        ("spoofed", 422),
        ("encoding", 415),
        ("bit_depth", 415),
        ("long", 413),
        ("large", 413),
    ],
)
def test_invalid_audio_never_calls_provider(
    application: FastAPI, api: API, wav_audio: bytes, wav_factory, kind: str, status: int
) -> None:
    audio = wav_audio
    if kind == "empty":
        audio = b""
    elif kind == "silent":
        audio = wav_factory(silent=True)
    elif kind == "truncated":
        audio = audio[:-1]
    elif kind == "spoofed":
        audio = b"dummy-sensitive-audio-in-not-a-wav-container"
    elif kind == "encoding":
        audio = bytearray(audio)
        struct.pack_into("<H", audio, 20, 3)
        audio = bytes(audio)
    elif kind == "bit_depth":
        audio = wav_factory(width=3)
    elif kind == "long":
        audio = wav_factory(frames=8000 * 60 + 1, rate=8000)
    elif kind == "large":
        audio = b"x" * (10485760 + 1)
    with TestClient(application) as client:
        response = client.post(ENDPOINT, content=audio, headers={"Content-Type": "audio/wav"})
    assert response.status_code == status
    assert api.requests == []
    assert "dummy-sensitive-audio" not in response.text


@pytest.mark.parametrize(
    "media_type",
    [
        None,
        "application/json",
        "audio/mp3",
        "audio/x-wav",
        "audio/webm",
        "video/webm",
        "multipart/form-data",
    ],
)
def test_unsupported_media_type(
    application: FastAPI, api: API, wav_audio: bytes, media_type: str | None
) -> None:
    headers = {"Content-Type": media_type} if media_type else {}
    with TestClient(application) as client:
        response = client.post(ENDPOINT, content=wav_audio, headers=headers)
    assert response.status_code == 415
    assert api.requests == []


def test_invalid_hint_is_redacted(application: FastAPI, api: API, wav_audio: bytes) -> None:
    with TestClient(application) as client:
        response = client.post(
            ENDPOINT,
            params={"language_hint": "dummy-private-hint"},
            content=wav_audio,
            headers={"Content-Type": "audio/wav"},
        )
    assert response.status_code == 422
    assert "dummy-private-hint" not in response.text
    assert all(set(error) == {"loc", "msg", "type"} for error in response.json()["detail"])
    assert api.requests == []


@pytest.mark.parametrize(
    "body, expected",
    [
        ({}, 503),
        (None, 503),
        ({"text": None}, 503),
        ({"text": " "}, 422),
        ({"text": "a" * 5001}, 503),
    ],
)
def test_invalid_provider_output_is_not_a_success(
    application: FastAPI, api: API, wav_audio: bytes, body: object, expected: int
) -> None:
    api.body = body
    with TestClient(application) as client:
        response = client.post(ENDPOINT, content=wav_audio, headers={"Content-Type": "audio/wav"})
    assert response.status_code == expected
    assert set(response.json()) == {"detail"}
    assert len(api.requests) == 1


@pytest.mark.parametrize("failure", ["timeout", "http_error", "json"])
def test_provider_failures_are_safe_without_retries(
    application: FastAPI, api: API, wav_audio: bytes, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    if failure == "timeout":
        api.failure = httpx.ReadTimeout("dummy-sensitive-provider-body")
    elif failure == "http_error":
        api.status = 429
        api.body = {"error": "dummy-sensitive-provider-body"}
    else:
        api.raw = b"invalid JSON: dummy-sensitive-provider-body"
    with caplog.at_level("DEBUG"), TestClient(application) as client:
        response = client.post(ENDPOINT, content=wav_audio, headers={"Content-Type": "audio/wav"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Audio transcription is unavailable."}
    assert len(api.requests) == 1
    for value in ("dummy-sensitive-provider-body", "dummy-groq-key", repr(wav_audio)):
        assert value not in response.text + caplog.text


def test_missing_key_is_safe(
    application: FastAPI, api: API, settings: Settings, wav_audio: bytes
) -> None:
    application.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"groq_api_key": Settings(_env_file=None).groq_api_key}
    )
    with TestClient(application) as client:
        response = client.post(ENDPOINT, content=wav_audio, headers={"Content-Type": "audio/wav"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Audio transcription is unavailable."}
    assert api.requests == []


async def asgi_request(application: FastAPI, headers: list[tuple[bytes, bytes]], receive):
    messages = []

    async def send(message):
        messages.append(message)

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": ENDPOINT,
            "raw_path": ENDPOINT.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": headers,
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 123),
        },
        receive,
        send,
    )
    return messages


@pytest.mark.parametrize("authorization", [None, b"Bearer", b"Bearer invalid", b"Basic invalid"])
@pytest.mark.parametrize("media_type", [b"audio/wav", b"audio/mp4", b"audio/mpeg"])
def test_authentication_happens_before_any_body_read(
    application: FastAPI, api: API, authorization: bytes | None, media_type: bytes, monkeypatch
) -> None:
    del application.dependency_overrides[get_current_user]

    async def receive():
        raise AssertionError("Unauthenticated request body must never be consumed")

    def forbidden(*args, **kwargs):
        raise AssertionError("Unauthenticated request must never start a conversion")

    monkeypatch.setattr(
        "app.modules.reporting.transcription.conversion.subprocess.Popen", forbidden
    )
    headers = [(b"content-type", media_type)]
    if authorization is not None:
        headers.append((b"authorization", authorization))
    messages = asyncio.run(asgi_request(application, headers, receive))
    assert messages[0]["status"] == 401
    assert api.requests == []


def test_auth_configuration_failure_remains_503_before_body_read(
    application: FastAPI, api: API
) -> None:
    del application.dependency_overrides[get_current_user]
    application.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)

    async def receive():
        raise AssertionError("Body must never be consumed during authentication failure")

    messages = asyncio.run(
        asgi_request(application, [(b"authorization", b"Bearer invalid")], receive)
    )
    assert messages[0]["status"] == 503
    assert api.requests == []


@pytest.mark.parametrize("header", [b"10485761", b"-1", b"bad", b"9" * 5000])
def test_bad_or_oversized_length_is_rejected_without_reading(
    application: FastAPI, api: API, header: bytes
) -> None:
    async def receive():
        raise AssertionError("Body must not be consumed")

    headers = [(b"content-type", b"audio/wav"), (b"content-length", header)]
    messages = asyncio.run(asgi_request(application, headers, receive))
    assert messages[0]["status"] == (413 if header == b"10485761" else 422)
    assert api.requests == []


@pytest.mark.parametrize("declared_length", [None, b"1"])
@pytest.mark.parametrize("media_type", [b"audio/wav", b"audio/mp4", b"audio/mpeg"])
def test_chunked_upload_enforces_actual_bytes(
    application: FastAPI,
    api: API,
    settings: Settings,
    wav_audio: bytes,
    declared_length: bytes | None,
    media_type: bytes,
) -> None:
    application.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"transcription_max_bytes": len(wav_audio)}
    )
    received = 0

    async def receive():
        nonlocal received
        received += 1
        if received == 1:
            return {"type": "http.request", "body": wav_audio, "more_body": True}
        if received == 2:
            return {"type": "http.request", "body": b"x", "more_body": True}
        raise AssertionError("Must not consume beyond the byte limit")

    headers = [(b"content-type", media_type)]
    if declared_length is not None:
        headers.append((b"content-length", declared_length))
    messages = asyncio.run(asgi_request(application, headers, receive))
    assert messages[0]["status"] == 413
    assert received == 2
    assert api.requests == []


def test_capacity_is_rejected_before_body_read(application: FastAPI, api: API) -> None:
    from app.modules.reporting.transcription.service import _TRANSCRIPTION_SLOTS

    assert _TRANSCRIPTION_SLOTS.acquire(blocking=False)
    assert _TRANSCRIPTION_SLOTS.acquire(blocking=False)
    try:

        async def receive():
            raise AssertionError("Busy endpoint must not consume audio")

        messages = asyncio.run(
            asgi_request(application, [(b"content-type", b"audio/wav")], receive)
        )
        assert messages[0]["status"] == 503
        assert api.requests == []
    finally:
        _TRANSCRIPTION_SLOTS.release()
        _TRANSCRIPTION_SLOTS.release()


def test_disconnect_is_safe(application: FastAPI, api: API) -> None:
    async def receive():
        return {"type": "http.disconnect"}

    messages = asyncio.run(asgi_request(application, [(b"content-type", b"audio/wav")], receive))
    assert messages[0]["status"] == 422
    assert api.requests == []


def test_openapi_describes_binary_audio_and_authentication(application: FastAPI) -> None:
    operation = application.openapi()["paths"][ENDPOINT]["post"]
    assert set(operation["requestBody"]["content"]) == set(SUPPORTED_MEDIA_TYPES)
    for entry in operation["requestBody"]["content"].values():
        assert entry["schema"]["format"] == "binary"
    assert operation["security"]


@pytest.mark.parametrize(
    "extension,media_type",
    [("m4a", "audio/mp4"), ("m4a", "audio/x-m4a"), ("mp4", "video/mp4"), ("mp3", "audio/mpeg")],
)
def test_real_compressed_audio_reaches_existing_provider_only_as_wav(
    application, api, media_factory, extension, media_type
):
    audio = media_factory(extension)
    with TestClient(application) as client:
        response = client.post(ENDPOINT, content=audio, headers={"Content-Type": media_type})
    assert response.status_code == 200
    assert response.json() == {
        "transcript": api.body["text"],
        "requested_language_hint": "unknown",
        "review_required": True,
    }
    assert len(api.requests) == 1
    body = api.requests[0].content
    assert b'filename="report.wav"' in body and b"RIFF" in body and b"WAVE" in body
    assert audio not in body


def test_missing_ffmpeg_returns_sanitized_503_and_wav_still_works(
    application, api, wav_audio, monkeypatch
):
    monkeypatch.setattr(
        "app.modules.reporting.transcription.conversion.shutil.which", lambda _: None
    )
    with TestClient(application) as client:
        response = client.post(
            ENDPOINT, content=b"synthetic", headers={"Content-Type": "audio/mp4"}
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "Audio transcription is unavailable."}
        assert not api.requests
        response = client.post(ENDPOINT, content=wav_audio, headers={"Content-Type": "audio/wav"})
        assert response.status_code == 200


@pytest.mark.parametrize("kind", ["corrupt", "mismatch", "no_audio", "long"])
def test_real_invalid_media_never_reaches_provider(application, api, media_factory, kind, caplog):
    audio = media_factory(
        "mp4", video_only=kind == "no_audio", duration=61 if kind == "long" else 0.25
    )
    if kind == "corrupt":
        audio = audio[:30]
    media_type = "audio/mpeg" if kind == "mismatch" else "video/mp4"
    with caplog.at_level("DEBUG"), TestClient(application) as client:
        response = client.post(ENDPOINT, content=audio, headers={"Content-Type": media_type})
    assert response.status_code == (413 if kind == "long" else 422)
    assert not api.requests
    assert (
        response.json() == {"detail": "Audio exceeds the permitted size or duration."}
        if kind == "long"
        else response.json() == {"detail": "Audio is invalid or contains no usable speech."}
    )
    assert repr(audio) not in response.text + caplog.text
