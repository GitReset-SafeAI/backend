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

    async def list_owned(self, reporter_id: UUID, limit: int, offset: int) -> list[ReportResponse]:
        """Return a page of reports belonging to the verified user."""
        if not isinstance(reporter_id, UUID):
            raise ReportPersistenceError
        fields = ("id", *ReportSubmission.model_fields, "submitted_at", "RAPID_SUBMISSIONS")
        try:
            response = await self.client.get(
                "reports",
                params={
                    "select": ",".join((*fields, "user_id")),
                    "user_id": f"eq.{reporter_id}",
                    "order": "submitted_at.desc,id.desc",
                    "limit": limit,
                    "offset": offset,
                },
            )
            response.raise_for_status()
            rows = response.json()
            if response.status_code != 200 or not isinstance(rows, list):
                raise ReportPersistenceError
            results = []
            for row in rows:
                if UUID(row["user_id"]) != reporter_id:
                    raise ReportPersistenceError
                results.append(ReportResponse.model_validate({name: row[name] for name in fields}))
            return results
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            raise ReportPersistenceError from None

    async def get_owned_translation(self, report_id: UUID, reporter_id: UUID) -> str | None:
        """Load minimal analysis input using both trusted ownership and report identity."""
        if not isinstance(report_id, UUID) or not isinstance(reporter_id, UUID):
            raise ReportPersistenceError
        try:
            response = await self.client.get(
                "reports",
                params={
                    "select": "id,user_id,translated_text",
                    "id": f"eq.{report_id}",
                    "user_id": f"eq.{reporter_id}",
                    "limit": "1",
                },
            )
            response.raise_for_status()
            rows = response.json()
            if response.status_code != 200 or not isinstance(rows, list) or len(rows) > 1:
                raise ReportPersistenceError
            if not rows:
                return None
            row = rows[0]
            if UUID(row["id"]) != report_id or UUID(row["user_id"]) != reporter_id:
                raise ReportPersistenceError
            text = row["translated_text"]
            if not isinstance(text, str) or not text.strip() or not 1 <= len(text) <= 5000:
                raise ReportPersistenceError
            return text
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            raise ReportPersistenceError from None

    async def create(self, submission: ReportSubmission, reporter_id: UUID) -> ReportResponse:
        """Insert all submitted fields, using database-generated identity and time."""
        if not isinstance(reporter_id, UUID):
            raise ReportPersistenceError

        payload = submission.model_dump(mode="json")
        payload["user_id"] = str(reporter_id)
        response_fields = (
            "id",
            *ReportSubmission.model_fields,
            "submitted_at",
            "RAPID_SUBMISSIONS",
        )
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
