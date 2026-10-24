"""OpenAI Responses API client for structured incident classification."""

import httpx
from pydantic import ValidationError

from app.modules.incident_ai.prompts import SYSTEM_PROMPT
from app.modules.incident_ai.schemas import IncidentAnalysis


class ClassificationError(RuntimeError):
    """Raised when the provider cannot return a valid structured analysis."""


class OpenAIIncidentClassifier:
    def __init__(self, api_key: str, model: str, base_url: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def classify(self, translated_text: str) -> IncidentAnalysis:
        if not self._api_key:
            raise ClassificationError("LLM API key is not configured")

        payload = {
            "model": self._model,
            "store": False,
            "instructions": SYSTEM_PROMPT,
            "input": translated_text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "incident_analysis",
                    "strict": True,
                    "schema": _response_schema(),
                }
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/responses",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                output_text = _extract_output_text(response.json())
                return IncidentAnalysis.model_validate_json(output_text)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ClassificationError("LLM analysis failed") from exc


def _extract_output_text(response: dict) -> str:
    if response.get("status") != "completed":
        raise ValueError("Response was not completed")
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "refusal":
                raise ValueError("Model refused the request")
            if content.get("type") == "output_text":
                return content["text"]
    raise ValueError("Response did not contain output text")


def _response_schema() -> dict:
    nullable_string = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "analysis_status": {"type": "string", "enum": ["completed", "needs_review"]},
            "category": {
                "type": "string",
                "enum": [
                    "unwanted_touching",
                    "sexual_remarks",
                    "stalking",
                    "threatening_behaviour",
                    "other",
                    "unclear",
                ],
            },
            "severity": {
                "type": "string",
                "enum": ["low", "moderate", "high", "critical", "undetermined"],
            },
            "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "extracted_details": {
                "type": "object",
                "properties": {
                    "summary": nullable_string,
                    "reported_behaviours": {"type": "array", "items": {"type": "string"}},
                    "reported_location": nullable_string,
                    "reported_time": nullable_string,
                    "immediate_danger_reported": {"type": ["boolean", "null"]},
                },
                "required": [
                    "summary",
                    "reported_behaviours",
                    "reported_location",
                    "reported_time",
                    "immediate_danger_reported",
                ],
                "additionalProperties": False,
            },
            "review_reason": nullable_string,
        },
        "required": [
            "analysis_status",
            "category",
            "severity",
            "confidence",
            "extracted_details",
            "review_reason",
        ],
        "additionalProperties": False,
    }
