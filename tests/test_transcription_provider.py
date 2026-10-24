"""Groq speech API contract and safe failures, using only mocked HTTP responses."""

import asyncio
from email import policy
from email.parser import BytesParser

import httpx
import pytest

from app.modules.reporting.transcription.provider import GroqTranscriber
from app.modules.reporting.transcription.service import InvalidAudio, TranscriptionError
from app.schemas.report import LanguageHint


class ProviderAPI:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.body: object = {"text": "  Synthetic private draft.\n"}
        self.status = 200
        self.failure: Exception | None = None
        self.raw: bytes | None = None
        self.delay = 0
        self.response_cleanup_failure = False
        self.client_cleanup_failure = False
        self.closed: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert str(request.url) == "https://provider.invalid/openai/v1/audio/transcriptions"
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        if self.response_cleanup_failure:
            api = self

            class Stream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield api.raw

                async def aclose(self):
                    api.closed.append("response")
                    raise httpx.ReadError("dummy-sensitive-response-cleanup")

            return httpx.Response(self.status, stream=Stream())
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw)
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> ProviderAPI:
    api = ProviderAPI()
    original = httpx.AsyncClient

    class Transport(httpx.MockTransport):
        async def aclose(self):
            await super().aclose()
            api.closed.append("client")
            if api.client_cleanup_failure:
                raise httpx.ReadError("dummy-sensitive-client-cleanup")

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(**kwargs, transport=Transport(api))

    monkeypatch.setattr("app.modules.reporting.transcription.provider.httpx.AsyncClient", client)
    return api


def adapter(**overrides) -> GroqTranscriber:
    return GroqTranscriber(
        **{
            "api_key": "dummy-groq-key",
            "model": "whisper-large-v3",
            "base_url": "https://provider.invalid/openai/v1",
            "timeout_seconds": 30.0,
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "hint, expected",
    [
        ("en", "en"),
        ("si", "si"),
        ("ta", "ta"),
        ("si-Latn", "si"),
        ("mixed", None),
        ("unknown", None),
    ],
)
def test_multipart_contains_only_audio_and_transcription_parameters(
    api: ProviderAPI, wav_audio: bytes, hint: str, expected: str | None
) -> None:
    result = asyncio.run(adapter().transcribe(wav_audio, LanguageHint(hint)))
    assert result == "  Synthetic private draft.\n"
    assert len(api.requests) == 1
    request = api.requests[0]
    assert request.headers["authorization"] == "Bearer dummy-groq-key"
    assert "apikey" not in request.headers
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
    )
    fields = {
        part.get_param("name", header="content-disposition"): part for part in message.iter_parts()
    }
    assert set(fields) == {"file", "model", "response_format", "temperature"} | (
        {"language"} if expected else set()
    )
    assert fields["file"].get_payload(decode=True) == wav_audio
    assert fields["file"].get_filename() == "report.wav"
    assert fields["model"].get_payload(decode=True) == b"whisper-large-v3"
    assert fields["response_format"].get_payload(decode=True) == b"json"
    if expected:
        assert fields["language"].get_payload(decode=True).decode() == expected


@pytest.mark.parametrize("body", [None, [], {}, {"text": None}, {"text": 3}, {"text": "a" * 5001}])
def test_malformed_or_oversized_transcripts(
    api: ProviderAPI, wav_audio: bytes, body: object, caplog
) -> None:
    api.body = body
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.SINHALA))
    assert error.value.status_code == 503
    assert len(api.requests) == 1
    if body is None:  # HTTPX json=None supplies an empty body, not the JSON literal null.
        expected = "stage=provider_response category=invalid_json"
    elif isinstance(body, dict) and "text" in body:
        expected = "stage=transcript_validation category=invalid_text"
    elif isinstance(body, dict):
        expected = "stage=provider_response category=missing_text"
    else:
        expected = "stage=provider_response category=invalid_response"
    events = [record for record in caplog.records if "transcription_failure" in record.getMessage()]
    assert len(events) == 1
    assert events[0].getMessage() == f"transcription_failure {expected}"
    assert events[0].exc_info is None and events[0].stack_info is None


@pytest.mark.parametrize("text", ["", " \n\t", "\u2003"])
def test_blank_transcript_means_no_usable_speech(
    api: ProviderAPI, wav_audio: bytes, text: str, caplog
) -> None:
    api.body = {"text": text}
    with pytest.raises(InvalidAudio):
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.UNKNOWN))
    assert "transcription_failure stage=transcript_validation category=unusable_text" in caplog.text


@pytest.mark.parametrize(
    "raw",
    [b"not JSON: dummy-private-error", b"x" * 65537],
    ids=["invalid-json", "over-response-limit"],
)
def test_bad_json_and_bounded_provider_response(
    api: ProviderAPI, wav_audio: bytes, raw: bytes, caplog
) -> None:
    api.raw = raw
    with pytest.raises(TranscriptionError):
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.TAMIL))
    category = "invalid_json" if len(raw) < 65536 else "response_too_large"
    events = [record for record in caplog.records if "transcription_failure" in record.getMessage()]
    assert len(events) == 1
    assert events[0].getMessage() == (
        f"transcription_failure stage=provider_response category={category}"
    )
    assert raw.decode() not in caplog.text


@pytest.mark.parametrize("status", [302, 400, 401, 403, 429, 500, 503])
def test_provider_errors_do_not_leak_or_retry(
    api: ProviderAPI, wav_audio: bytes, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    api.status = status
    api.body = {"error": "dummy-sensitive-provider-body"}
    with caplog.at_level("DEBUG"), pytest.raises(TranscriptionError) as error:
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.SINHALA))
    assert error.value.status_code == 503
    assert len(api.requests) == 1
    events = [record for record in caplog.records if "transcription_failure" in record.getMessage()]
    assert len(events) == 1
    assert events[0].getMessage() == (
        f"transcription_failure stage=provider_request category=http_status status={status}"
    )
    assert events[0].args == ("provider_request", "http_status", status)
    assert events[0].exc_info is None and events[0].stack_info is None
    for value in ("dummy-sensitive-provider-body", "dummy-groq-key", repr(wav_audio)):
        assert value not in caplog.text + str(error.value)


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_failure_is_sanitized(
    api: ProviderAPI, wav_audio: bytes, failure, caplog
) -> None:
    api.failure = failure("dummy-sensitive-transport-detail")
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.ENGLISH))
    assert str(error.value) == ""
    assert len(api.requests) == 1
    category = "timeout" if failure is httpx.ReadTimeout else "transport_error"
    assert f"transcription_failure stage=provider_request category={category}" in caplog.text
    assert "dummy-sensitive-transport-detail" not in caplog.text


def test_total_provider_deadline(api: ProviderAPI, wav_audio: bytes, caplog) -> None:
    api.delay = 1
    with pytest.raises(TranscriptionError):
        asyncio.run(adapter(timeout_seconds=0.01).transcribe(wav_audio, LanguageHint.UNKNOWN))
    assert len(api.requests) == 1
    assert "transcription_failure stage=provider_request category=timeout" in caplog.text


def test_unpaired_unicode_surrogate_is_a_safe_provider_failure(
    api: ProviderAPI, wav_audio: bytes, caplog
) -> None:
    api.raw = b'{"text":"\\ud800"}'
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.UNKNOWN))
    assert error.value.status_code == 503
    assert str(error.value) == ""
    assert (
        "transcription_failure stage=transcript_validation category=invalid_encoding" in caplog.text
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": ""},
        {"model": " "},
        {"base_url": "http://provider.invalid"},
        {"base_url": "https://user:dummy-password@provider.invalid"},
        {"base_url": "https://provider.invalid?secret=dummy"},
        {"base_url": "https://provider.invalid:bad"},
        {"base_url": "https://provider.invalid:65536"},
        {"base_url": "https://provider.invalid:-1"},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
    ],
)
def test_missing_or_invalid_configuration_makes_zero_calls(
    api: ProviderAPI, wav_audio: bytes, overrides, caplog
) -> None:
    with pytest.raises(TranscriptionError):
        asyncio.run(adapter(**overrides).transcribe(wav_audio, LanguageHint.UNKNOWN))
    assert api.requests == []
    events = [record for record in caplog.records if "transcription_failure" in record.getMessage()]
    assert len(events) == 1
    assert events[0].getMessage() == (
        "transcription_failure stage=provider_configuration category=invalid_configuration"
    )
    assert events[0].args == ("provider_configuration", "invalid_configuration")
    assert events[0].exc_info is None and events[0].stack_info is None
    for secret in ("dummy-password", "dummy-groq-key", repr(wav_audio)):
        assert secret not in caplog.text


@pytest.mark.parametrize("cleanup", ["response", "client", "both"])
@pytest.mark.parametrize("failure", ["oversized", "http_status"])
def test_processing_failure_remains_primary_when_http_cleanup_fails(
    api: ProviderAPI, wav_audio: bytes, cleanup: str, failure: str, caplog
) -> None:
    api.response_cleanup_failure = cleanup in {"response", "both"}
    api.client_cleanup_failure = cleanup in {"client", "both"}
    api.status = 429 if failure == "http_status" else 200
    api.raw = b"dummy-sensitive-provider-body" * (3000 if failure == "oversized" else 1)
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.UNKNOWN))
    assert error.value.status_code == 503
    assert error.value.detail == "Audio transcription is unavailable."
    assert len(api.requests) == 1
    assert api.closed == (["response", "client"] if api.response_cleanup_failure else ["client"])
    events = [record for record in caplog.records if "transcription_failure" in record.getMessage()]
    assert len(events) == 1
    expected = (
        ("provider_request", "http_status", 429)
        if failure == "http_status"
        else ("provider_response", "response_too_large")
    )
    suffix = " status=429" if failure == "http_status" else ""
    assert events[0].getMessage() == (
        f"transcription_failure stage={expected[0]} category={expected[1]}{suffix}"
    )
    assert events[0].args == expected
    assert events[0].exc_info is None and events[0].exc_text is None
    assert events[0].stack_info is None
    assert all(isinstance(value, (str, int)) for value in events[0].args)
    for sensitive in (
        "dummy-sensitive-provider-body",
        "dummy-sensitive-response-cleanup",
        "dummy-sensitive-client-cleanup",
        "dummy-groq-key",
        "Authorization",
        repr(wav_audio),
    ):
        assert sensitive not in caplog.text + str(error.value)


@pytest.mark.parametrize(
    "text", ["a", "a" * 5000, "  කෙනෙක් පස්සෙන් ආවා.\n", " ஒருவர் என்னைப் பின்தொடர்ந்தார். "]
)
def test_transcript_boundaries_and_unicode_are_preserved(
    api: ProviderAPI, wav_audio: bytes, text: str
) -> None:
    api.body = {"text": text}
    assert asyncio.run(adapter().transcribe(wav_audio, LanguageHint.UNKNOWN)) == text
