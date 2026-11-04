"""Public Supabase Auth operations; never use admin account creation."""

import logging
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Never log provider messages: they can contain email addresses or SQL details.
_SAFE_ERROR_CODES = frozenset(
    {
        "unexpected_failure",
        "signup_disabled",
        "email_provider_disabled",
        "email_address_not_authorized",
        "email_address_invalid",
        "email_exists",
        "user_already_exists",
        "weak_password",
        "validation_failed",
        "captcha_failed",
        "hook_timeout",
        "hook_timeout_after_retry",
        "hook_payload_over_size_limit",
        "hook_payload_invalid_content_type",
        "over_request_rate_limit",
        "over_email_send_rate_limit",
        "email_not_confirmed",
        "invalid_credentials",
        "request_timeout",
    }
)


def _failure_reason(data: dict) -> str:
    """Recognize legacy Auth error messages; return only fixed diagnostic labels."""
    for field in ("msg", "message", "error_description", "error"):
        message = data.get(field)
        if not isinstance(message, str):
            continue
        message = message[:4096].lower()
        if "error sending confirmation email" in message:
            return "confirmation_email_delivery_failed"
        if (
            "database error saving new user" in message
            or "database error creating new user" in message
        ):
            return "database_user_creation_failed"
        if "email address not authorized" in message:
            return "email_recipient_not_authorized"
        if "hook" in message and ("timeout" in message or "failed" in message):
            return "auth_hook_failed"
    return "unclassified"


class AuthenticationService:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.supabase_url.rstrip("/")
        self.key = settings.supabase_publishable_key.get_secret_value()
        try:
            parsed = urlsplit(self.base_url)
            _ = parsed.port
            valid = (
                parsed.scheme == "https"
                and parsed.hostname
                and not any(
                    (parsed.username, parsed.password, parsed.path, parsed.query, parsed.fragment)
                )
            )
        except ValueError:
            valid = False
        if not valid or not self.key:
            logger.warning("Supabase Auth unavailable: invalid URL or missing publishable key")
            raise HTTPException(503, "Authentication is unavailable.")

    async def post(self, path: str, payload: dict, *, signup: bool = False) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=10.0, trust_env=False, follow_redirects=False
            ) as client:
                response = await client.post(
                    f"{self.base_url}/auth/v1/{path}",
                    headers={"apikey": self.key},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            logger.warning("Supabase Auth transport failure: %s", type(exc).__name__)
            raise HTTPException(503, "Authentication is unavailable.") from None

        try:
            data = response.json()
        except ValueError:
            logger.warning("Supabase Auth returned non-JSON data: status=%s", response.status_code)
            raise HTTPException(503, "Authentication is unavailable.") from None
        if not isinstance(data, dict):
            logger.warning("Supabase Auth returned an invalid response object")
            raise HTTPException(503, "Authentication is unavailable.")
        code = data.get("code") or data.get("error_code")
        if not response.is_success:
            safe_code = code if isinstance(code, str) and code in _SAFE_ERROR_CODES else "unknown"
            logger.warning(
                "Supabase Auth rejected %s: status=%s code=%s reason=%s. "
                "For server errors, check Supabase Dashboard > Authentication > Logs "
                "(email delivery/SMTP, database triggers, or Auth hooks).",
                "signup" if signup else "login",
                response.status_code,
                safe_code,
                _failure_reason(data),
            )
        if response.status_code == 429:
            raise HTTPException(429, "Too many attempts. Please try again later.")
        # Preserve the same generic result for existing and new email addresses.
        if signup and code in ("user_already_exists", "email_exists"):
            return {}
        if not response.is_success:
            if not signup and code == "email_not_confirmed":
                raise HTTPException(403, "Confirm your email before logging in.")
            if not signup and code == "invalid_credentials":
                raise HTTPException(401, "Invalid email or password.")
            if signup and response.status_code in {400, 422}:
                raise HTTPException(422, "Registration could not be completed. Check your details.")
            raise HTTPException(503, "Authentication is unavailable.")
        if signup:
            # Confirmations on returns a user; confirmations off returns a session.
            try:
                UUID(data.get("user", data)["id"])
            except (KeyError, ValueError, TypeError, AttributeError):
                logger.warning("Supabase Auth signup returned no valid user ID")
                raise HTTPException(503, "Authentication is unavailable.") from None
        return data
