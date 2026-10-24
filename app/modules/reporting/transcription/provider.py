"""Groq speech-to-text transport; no database, translation, or classification calls."""

import asyncio
import json
import logging
import math
from contextlib import AsyncExitStack
from urllib.parse import urlsplit

import httpx

from app.modules.reporting.transcription.service import TranscriptionError, validate_transcript
from app.schemas.report import LanguageHint

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 65536
_HANDLED_ERRORS = (
    TranscriptionError,
    httpx.HTTPError,
    httpx.InvalidURL,
    TimeoutError,
    ValueError,
    TypeError,
    KeyError,
    RecursionError,
)
PROVIDER_LANGUAGES = {
    LanguageHint.ENGLISH: "en",
    LanguageHint.SINHALA: "si",
    LanguageHint.TAMIL: "ta",
    LanguageHint.SINHALA_LATIN: "si",
}


def _diagnostic(error: Exception, stage: str, category: str) -> tuple[str, str, int | None]:
    """Keep only fixed identifiers/status, never an exception or provider payload."""
    if isinstance(error, httpx.HTTPStatusError):
        return "provider_request", "http_status", error.response.status_code
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return "provider_request", "timeout", None
    if isinstance(error, httpx.HTTPError):
        return "provider_request", "transport_error", None
    return stage, category, None


class GroqTranscriber:
    def __init__(self, api_key: str, model: str, base_url: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def transcribe(self, audio: bytes, language_hint: LanguageHint) -> str:
        stage = "provider_configuration"
        category = "invalid_configuration"
        primary: tuple[str, str, int | None] | None = None
        try:
            url = urlsplit(self._base_url)
            _ = url.port  # urllib validates port syntax and range on access.
            httpx.URL(self._base_url)  # Validate HTTPX URL components before client access.
            if (
                not self._api_key.strip()
                or not self._model.strip()
                or url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
                or not math.isfinite(self._timeout_seconds)
                or self._timeout_seconds <= 0
            ):
                raise TranscriptionError
            data = {"model": self._model, "response_format": "json", "temperature": "0"}
            if language_hint in PROVIDER_LANGUAGES:
                data["language"] = PROVIDER_LANGUAGES[language_hint]
            stage = "provider_request"
            category = "invalid_request"
            async with asyncio.timeout(self._timeout_seconds):
                async with AsyncExitStack() as resources:
                    try:
                        client = await resources.enter_async_context(
                            httpx.AsyncClient(
                                timeout=self._timeout_seconds,
                                trust_env=False,
                                follow_redirects=False,
                            )
                        )
                        response = await resources.enter_async_context(
                            client.stream(
                                "POST",
                                f"{self._base_url}/audio/transcriptions",
                                headers={"Authorization": f"Bearer {self._api_key}"},
                                data=data,
                                files={"file": ("report.wav", audio, "audio/wav")},
                            )
                        )
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(chunk) > MAX_RESPONSE_BYTES - len(body):
                                stage, category = "provider_response", "response_too_large"
                                raise TranscriptionError
                            body.extend(chunk)
                    except _HANDLED_ERRORS as error:
                        # Capture before either HTTP context exits. Cleanup may replace
                        # the raised exception, but must not replace its diagnostic.
                        primary = _diagnostic(error, stage, category)
                        raise
            stage = "provider_response"
            category = "invalid_json"
            result = json.loads(body)
            category = "invalid_response"
            if not isinstance(result, dict):
                raise TranscriptionError
            if "text" not in result:
                category = "missing_text"
                raise TranscriptionError
            text = result["text"]
        except _HANDLED_ERRORS as error:
            if primary is None:
                primary = _diagnostic(error, stage, category)
            if isinstance(error, TranscriptionError):
                raise
            raise TranscriptionError from None
        finally:
            if primary is not None:
                failed_stage, failed_category, status = primary
                if status is None:
                    logger.warning(
                        "transcription_failure stage=%s category=%s", failed_stage, failed_category
                    )
                else:
                    logger.warning(
                        "transcription_failure stage=%s category=%s status=%d",
                        failed_stage,
                        failed_category,
                        status,
                    )
        # This validator emits its own single diagnostic, after HTTP cleanup finishes.
        return validate_transcript(text)
