"""Groq speech-to-text transport; no database, translation, or classification calls."""

import asyncio
import json
import math
from urllib.parse import urlsplit

import httpx

from app.modules.reporting.transcription.service import TranscriptionError, validate_transcript
from app.schemas.report import LanguageHint

MAX_RESPONSE_BYTES = 65536
PROVIDER_LANGUAGES = {
    LanguageHint.ENGLISH: "en",
    LanguageHint.SINHALA: "si",
    LanguageHint.TAMIL: "ta",
    LanguageHint.SINHALA_LATIN: "si",
}


class GroqTranscriber:
    def __init__(self, api_key: str, model: str, base_url: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def transcribe(self, audio: bytes, language_hint: LanguageHint) -> str:
        try:
            url = urlsplit(self._base_url)
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
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, trust_env=False, follow_redirects=False
                ) as client:
                    async with client.stream(
                        "POST",
                        f"{self._base_url}/audio/transcriptions",
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        data=data,
                        files={"file": ("report.wav", audio, "audio/wav")},
                    ) as response:
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(chunk) > MAX_RESPONSE_BYTES - len(body):
                                raise TranscriptionError
                            body.extend(chunk)
            result = json.loads(body)
            if not isinstance(result, dict):
                raise TranscriptionError
            return validate_transcript(result["text"])
        except (
            httpx.HTTPError,
            httpx.InvalidURL,
            TimeoutError,
            ValueError,
            TypeError,
            KeyError,
            RecursionError,
        ):
            raise TranscriptionError from None
