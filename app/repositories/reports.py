"""Report creation through the server-side Supabase Data API."""

from uuid import UUID

import httpx

from app.schemas.report import ReportResponse, ReportSubmission


class ReportPersistenceError(Exception):
    """Storage failed or did not return a complete, validated saved report."""


class ReportRepository:
    """Persist reports with an owner supplied by trusted backend code."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def create(self, submission: ReportSubmission, reporter_id: UUID) -> ReportResponse:
        """Insert all submitted fields, using database-generated identity and time."""
        if not isinstance(reporter_id, UUID):
            raise ReportPersistenceError

        payload = submission.model_dump(mode="json")
        payload["user_id"] = str(reporter_id)
        response_fields = ("id", *ReportSubmission.model_fields, "submitted_at")
        try:
            response = await self.client.post(
                "reports",
                params={"select": ",".join((*response_fields, "user_id"))},
                headers={"Prefer": "return=representation"},
                json=payload,
            )
            response.raise_for_status()
            if response.status_code != 201:
                raise ReportPersistenceError
            rows = response.json()
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise ReportPersistenceError

            row = rows[0]
            if UUID(row["user_id"]) != reporter_id:
                raise ReportPersistenceError
            saved = ReportResponse.model_validate({name: row[name] for name in response_fields})
            if any(
                getattr(saved, name) != getattr(submission, name)
                for name in ReportSubmission.model_fields
            ):
                raise ReportPersistenceError
            return saved
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            # Provider error bodies and validation inputs may contain private data.
            raise ReportPersistenceError from None
