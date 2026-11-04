"""User-scoped contact storage through Supabase RLS."""

from uuid import UUID

import httpx
from fastapi import HTTPException

from app.schemas.emergency_contacts import ContactInput, EmergencyContact


class EmergencyContactRepository:
    def __init__(self, client: httpx.AsyncClient, owner: UUID) -> None:
        self.client = client
        self.owner = owner

    async def _request(self, method: str, path: str, **kwargs) -> list[dict]:
        try:
            response = await self.client.request(method, path, **kwargs)
            if response.status_code == 409:
                raise HTTPException(409, "You can save up to five emergency contacts.")
            response.raise_for_status()
            rows = response.json()
            if not response.is_success or not isinstance(rows, list):
                raise ValueError
            for row in rows:
                if UUID(row["user_id"]) != self.owner:
                    raise ValueError
                self._contact(row)
            return rows
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(503, "Emergency contact storage is unavailable.") from None

    @staticmethod
    def _contact(row: dict) -> EmergencyContact:
        return EmergencyContact.model_validate(
            {key: row[key] for key in ("id", "name", "phone", "relationship", "slot")}
        )

    async def list(self) -> list[EmergencyContact]:
        rows = await self._request(
            "GET",
            "emergency_contacts",
            params={
                "user_id": f"eq.{self.owner}",
                "order": "slot.asc",
                "limit": "5",
            },
        )
        return [self._contact(row) for row in rows]

    async def add(self, contact: ContactInput) -> EmergencyContact:
        rows = await self._request(
            "POST",
            "rpc/add_emergency_contact",
            json={
                "p_name": contact.name,
                "p_phone": contact.phone,
                "p_relationship": contact.relationship,
            },
        )
        if len(rows) != 1:
            raise HTTPException(503, "Emergency contact storage is unavailable.")
        return self._contact(rows[0])

    async def change(self, contact_id: UUID, fields: dict | None) -> EmergencyContact:
        if fields is None:
            rows = await self._request(
                "POST",
                "rpc/remove_emergency_contact",
                json={"p_contact_id": str(contact_id)},
            )
        else:
            rows = await self._request(
                "PATCH",
                "emergency_contacts",
                params={"id": f"eq.{contact_id}", "user_id": f"eq.{self.owner}"},
                headers={"Prefer": "return=representation"},
                json=fields,
            )
        if not rows:
            raise HTTPException(404, "Emergency contact not found.")
        if len(rows) != 1 or str(rows[0]["id"]) != str(contact_id):
            raise HTTPException(503, "Emergency contact storage is unavailable.")
        return self._contact(rows[0])

    async def make_primary(self, contact_id: UUID) -> None:
        rows = await self._request(
            "POST",
            "rpc/make_primary_emergency_contact",
            json={"p_contact_id": str(contact_id)},
        )
        if not rows:
            raise HTTPException(404, "Emergency contact not found.")
        if len(rows) != 1 or str(rows[0]["id"]) != str(contact_id) or rows[0]["slot"] != 0:
            raise HTTPException(503, "Emergency contact storage is unavailable.")
