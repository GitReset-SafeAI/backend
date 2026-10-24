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

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert str(request.url) == "https://provider.invalid/openai/v1/audio/transcriptions"
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw)
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> ProviderAPI:
    api = ProviderAPI()
    original = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(api))

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
    api: ProviderAPI, wav_audio: bytes, body: object
) -> None:
    api.body = body
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.SINHALA))
    assert error.value.status_code == 503
    assert len(api.requests) == 1


@pytest.mark.parametrize("text", ["", " \n\t", "\u2003"])
def test_blank_transcript_means_no_usable_speech(
    api: ProviderAPI, wav_audio: bytes, text: str
) -> None:
    api.body = {"text": text}
    with pytest.raises(InvalidAudio):
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.UNKNOWN))


@pytest.mark.parametrize(
    "raw",
    [b"not JSON: dummy-private-error", b"x" * 65537],
    ids=["invalid-json", "over-response-limit"],
)
def test_bad_json_and_bounded_provider_response(
    api: ProviderAPI, wav_audio: bytes, raw: bytes
) -> None:
    api.raw = raw
    with pytest.raises(TranscriptionError):
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.TAMIL))


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
    for value in ("dummy-sensitive-provider-body", "dummy-groq-key", repr(wav_audio)):
        assert value not in caplog.text + str(error.value)


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_failure_is_sanitized(api: ProviderAPI, wav_audio: bytes, failure) -> None:
    api.failure = failure("dummy-sensitive-transport-detail")
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.ENGLISH))
    assert str(error.value) == ""
    assert len(api.requests) == 1


def test_total_provider_deadline(api: ProviderAPI, wav_audio: bytes) -> None:
    api.delay = 1
    with pytest.raises(TranscriptionError):
        asyncio.run(adapter(timeout_seconds=0.01).transcribe(wav_audio, LanguageHint.UNKNOWN))
    assert len(api.requests) == 1


def test_unpaired_unicode_surrogate_is_a_safe_provider_failure(
    api: ProviderAPI, wav_audio: bytes
) -> None:
    api.raw = b'{"text":"\\ud800"}'
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(adapter().transcribe(wav_audio, LanguageHint.UNKNOWN))
    assert error.value.status_code == 503
    assert str(error.value) == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": ""},
        {"model": " "},
        {"base_url": "http://provider.invalid"},
        {"base_url": "https://user:dummy-password@provider.invalid"},
        {"base_url": "https://provider.invalid?secret=dummy"},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
    ],
)
def test_missing_or_invalid_configuration_makes_zero_calls(
    api: ProviderAPI, wav_audio: bytes, overrides
) -> None:
    with pytest.raises(TranscriptionError):
        asyncio.run(adapter(**overrides).transcribe(wav_audio, LanguageHint.UNKNOWN))
    assert api.requests == []


@pytest.mark.parametrize(
    "text", ["a", "a" * 5000, "  කෙනෙක් පස්සෙන් ආවා.\n", " ஒருவர் என்னைப் பின்தொடர்ந்தார். "]
)
def test_transcript_boundaries_and_unicode_are_preserved(
    api: ProviderAPI, wav_audio: bytes, text: str
) -> None:
    api.body = {"text": text}
    assert asyncio.run(adapter().transcribe(wav_audio, LanguageHint.UNKNOWN)) == text
