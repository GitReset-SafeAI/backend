"""Groq chat-completions client for structured incident classification."""

import httpx
from pydantic import ValidationError

from app.modules.incident_ai.prompts import SYSTEM_PROMPT
from app.modules.incident_ai.schemas import AnalysisOutput


class ClassificationError(RuntimeError):
    """Raised when the provider cannot return a valid structured analysis."""


class GroqIncidentClassifier:
    def __init__(self, api_key: str, model: str, base_url: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def classify(self, translated_text: str) -> AnalysisOutput:
        if not self._api_key:
            raise ClassificationError("LLM API key is not configured")

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": translated_text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "incident_analysis",
                    "strict": True,
                    "schema": _response_schema(),
                },
            },
            "temperature": 0,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                output_text = _extract_message_content(response.json())
                return AnalysisOutput.model_validate_json(output_text)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ClassificationError("LLM analysis failed") from exc


def _extract_message_content(response: dict) -> str:
    choices = response.get("choices", [])
    if not choices:
        raise ValueError("Response did not contain a choice")
    message = choices[0].get("message", {})
    if message.get("refusal"):
        raise ValueError("Model refused the request")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise ValueError("Response did not contain message content")
    return content


def _response_schema() -> dict:
    return AnalysisOutput.model_json_schema()
