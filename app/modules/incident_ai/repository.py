"""Persist analysis records through Supabase PostgREST."""

import httpx

from app.modules.incident_ai.schemas import IncidentAnalysis


class AnalysisStorageError(RuntimeError):
    """An analysis could not be saved or its saved representation read."""


class SupabaseAnalysisRepository:
    def __init__(self, url: str, service_key: str) -> None:
        self._url = url.rstrip("/")
        self._service_key = service_key

    async def list_analyses(self, limit: int, offset: int) -> list[dict]:
        """Read every stored column, including historical analysis representations."""
        if not self._url or not self._service_key:
            raise AnalysisStorageError("Supabase backend credentials are not configured")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{self._url}/rest/v1/incident_ai_analyses",
                    headers={
                        "apikey": self._service_key,
                        "Authorization": f"Bearer {self._service_key}",
                    },
                    params={
                        "select": "*",
                        "order": "created_at.desc,id.desc",
                        "limit": limit,
                        "offset": offset,
                    },
                )
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError("Expected analysis rows")
                return rows
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise AnalysisStorageError("Could not retrieve analyses") from exc

    async def save(self, analysis: IncidentAnalysis) -> IncidentAnalysis:
        if not self._url or not self._service_key:
            raise AnalysisStorageError("Supabase backend credentials are not configured")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{self._url}/rest/v1/incident_ai_analyses",
                    headers={
                        "apikey": self._service_key,
                        "Authorization": f"Bearer {self._service_key}",
                        "Prefer": "return=representation",
                    },
                    json=analysis.model_dump(mode="json"),
                )
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list) or len(rows) != 1:
                    raise ValueError("Expected one saved analysis")
                return IncidentAnalysis.model_validate(rows[0])
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise AnalysisStorageError("Could not persist analysis") from exc
