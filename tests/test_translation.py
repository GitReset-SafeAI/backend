"""Synthetic Groq translation responses; every provider request uses MockTransport."""

import asyncio
import json

import httpx
import pytest

from app.modules.reporting.transcription.translation import GroqReportTranslator, TranslationError
from app.schemas.report import LanguageHint

ENGLISH = "Someone may have followed me. I am not sure."


def completion(
    text: object = ENGLISH, *, status: str = "translated", finish_reason: str = "stop"
) -> dict:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "content": json.dumps({"status": status, "translated_text": text}),
                },
            }
        ]
    }


class Provider:
    def __init__(self) -> None:
        self.body: object = completion()
        self.status_code = 200
        self.failure: httpx.HTTPError | None = None
        self.requests: list[httpx.Request] = []
        self.raw_body: bytes | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url == "https://provider.invalid/openai/v1/chat/completions"
        if self.failure:
            raise self.failure
        if self.raw_body is not None:
            return httpx.Response(self.status_code, content=self.raw_body)
        return httpx.Response(self.status_code, json=self.body)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Provider:
    provider = Provider()
    original_client = httpx.AsyncClient

    def mock_client(**kwargs: object) -> httpx.AsyncClient:
        assert kwargs == {"timeout": 12.0, "trust_env": False, "follow_redirects": False}
        return original_client(**kwargs, transport=httpx.MockTransport(provider))

    monkeypatch.setattr(
        "app.modules.reporting.transcription.translation.httpx.AsyncClient", mock_client
    )
    return provider


def translator(**overrides: object) -> GroqReportTranslator:
    return GroqReportTranslator(
        **{
            "api_key": "dummy-groq-key",
            "model": "dummy-model",
            "base_url": "https://provider.invalid/openai/v1",
            "timeout_seconds": 12.0,
            **overrides,
        }
    )


@pytest.mark.parametrize(
    ("hint", "text"),
    [
        (LanguageHint.SINHALA, "  කෙනෙක් මගේ පස්සෙන් ආවා.\n"),
        (LanguageHint.TAMIL, "  ஒருவர் என்னைப் பின்தொடர்ந்தார்.\n"),
        (LanguageHint.SINHALA_LATIN, "  kenek mage passen aawa.\n"),
        (LanguageHint.MIXED, "  බස් එකෙන් බැස්සාට පස්සේ someone followed me.\n"),
        (LanguageHint.UNKNOWN, "  kenek mage passen aawa.\n"),
    ],
)
def test_multilingual_translation_sends_only_text_and_hint(
    provider: Provider, hint: LanguageHint, text: str
) -> None:
    assert asyncio.run(translator().translate(text, hint)) == ENGLISH
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.headers["authorization"] == "Bearer dummy-groq-key"
    payload = json.loads(request.content)
    assert payload["model"] == "dummy-model"
    assert payload["temperature"] == 0
    assert len(payload["messages"]) == 2
    assert json.loads(payload["messages"][1]["content"]) == {
        "original_text": text,
        "language_hint": hint.value,
    }
    assert "Latin script alone does not establish English" in payload["messages"][0]["content"]
    schema = payload["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "not JSON"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"refusal": "Refused"}}]},
        completion(""),
        completion(" \n\t"),
        completion(None),
        completion(123),
        completion("x" * 5001),
        completion(status="unable_to_translate"),
        completion(None, status="unable_to_translate"),
        completion(status="unknown"),
        completion(finish_reason="length"),
        completion(finish_reason="content_filter"),
        completion("කෙනෙක් මගේ පස්සෙන් ආවා."),
        completion("ஒருவர் என்னைப் பின்தொடர்ந்தார்."),
    ],
)
def test_invalid_or_unreliable_provider_output_fails(provider: Provider, body: object) -> None:
    provider.body = body
    with pytest.raises(TranslationError):
        asyncio.run(translator().translate("Synthetic report", LanguageHint.UNKNOWN))
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    "hint", [LanguageHint.SINHALA, LanguageHint.TAMIL, LanguageHint.SINHALA_LATIN]
)
def test_non_english_echo_is_not_a_translation(provider: Provider, hint: LanguageHint) -> None:
    text = "Synthetic untranslated source"
    provider.body = completion(text)
    with pytest.raises(TranslationError):
        asyncio.run(translator().translate(f"  {text}\n", hint))


@pytest.mark.parametrize("hint", [LanguageHint.MIXED, LanguageHint.UNKNOWN])
def test_multilingual_path_can_return_already_english_text(
    provider: Provider, hint: LanguageHint
) -> None:
    assert asyncio.run(translator().translate(ENGLISH, hint)) == ENGLISH


@pytest.mark.parametrize("length", [1, 5000])
def test_translation_length_boundaries(provider: Provider, length: int) -> None:
    provider.body = completion("a" * length)
    assert (
        len(asyncio.run(translator().translate("Synthetic source", LanguageHint.TAMIL))) == length
    )


@pytest.mark.parametrize("status", [301, 400, 401, 403, 429, 500, 503])
def test_provider_http_error_is_sanitized(provider: Provider, status: int) -> None:
    provider.status_code = status
    provider.body = {"error": "dummy-private-provider-body"}
    with pytest.raises(TranslationError) as error:
        asyncio.run(translator().translate("Synthetic source", LanguageHint.TAMIL))
    assert "dummy-private-provider-body" not in str(error.value)
    assert len(provider.requests) == 1


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError])
def test_provider_outage_is_sanitized(
    provider: Provider, failure: type[httpx.HTTPError], caplog: pytest.LogCaptureFixture
) -> None:
    provider.failure = failure("dummy-sensitive-report-and-key")
    with caplog.at_level("INFO"), pytest.raises(TranslationError) as error:
        asyncio.run(translator().translate("Synthetic private source", LanguageHint.SINHALA))
    assert str(error.value) == ""
    assert "dummy-sensitive-report-and-key" not in caplog.text
    assert "Synthetic private source" not in caplog.text
    assert "dummy-groq-key" not in caplog.text
    assert len(provider.requests) == 1


def test_malformed_provider_json_is_sanitized(provider: Provider) -> None:
    provider.raw_body = b"not JSON: dummy-private-provider-body"
    with pytest.raises(TranslationError):
        asyncio.run(translator().translate("Synthetic source", LanguageHint.SINHALA))


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": ""},
        {"api_key": " "},
        {"model": ""},
        {"base_url": ""},
        {"base_url": "http://provider.invalid"},
        {"base_url": "https://user:dummy-password@provider.invalid"},
        {"base_url": "https://provider.invalid?credential=dummy"},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1},
        {"timeout_seconds": float("nan")},
    ],
)
def test_unavailable_configuration_never_calls_provider(
    provider: Provider, overrides: dict
) -> None:
    with pytest.raises(TranslationError):
        asyncio.run(translator(**overrides).translate("Synthetic source", LanguageHint.TAMIL))
    assert provider.requests == []
