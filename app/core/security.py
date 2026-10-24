"""Supabase asymmetric access-token verification, independent of database credentials."""

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import jwt

ALGORITHMS = ("ES256", "RS256")
JWKS_TTL_SECONDS = 600
JWKS_REFRESH_INTERVAL_SECONDS = 30
MAX_JWKS_BYTES = 131072
MAX_JWKS_KEYS = 32


class InvalidCredentials(Exception):
    """Credentials cannot authenticate a user."""


class AuthenticationUnavailable(Exception):
    """Trusted verification configuration or keys are unavailable."""


class InsufficientPermissions(Exception):
    """A verified token does not have the authenticated-user role."""


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Only the verified identity needed by protected modules; no raw claims."""

    id: UUID


class SupabaseTokenVerifier:
    """Bounded per-process JWKS cache with serialised, rate-limited refreshes.

    Used from FastAPI's synchronous dependency thread pool. No token-supplied
    URLs are fetched, no stale keys are accepted after TTL, and failed refreshes
    are also subject to the global refresh interval.
    """

    def __init__(self, supabase_url: str, jwks_url: str) -> None:
        base = supabase_url.rstrip("/")
        parsed = urlsplit(base)
        # Accessing port validates malformed/non-numeric/out-of-range ports.
        _ = parsed.port
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or jwks_url != f"{base}/auth/v1/.well-known/jwks.json"
        ):
            raise AuthenticationUnavailable
        self.issuer = f"{base}/auth/v1"
        self.jwks_url = jwks_url
        self._keys: dict[str, jwt.PyJWK] = {}
        self._expires_at = 0.0
        self._refresh_after = 0.0
        self._refresh_failed = False
        self._lock = Lock()

    def verify(self, token: str) -> AuthenticatedUser:
        try:
            if len(token) > 16384 or any(char.isspace() for char in token):
                raise InvalidCredentials
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            kid = header.get("kid")
            if (
                algorithm not in ALGORITHMS
                or not isinstance(kid, str)
                or not 1 <= len(kid) <= 256
                or header.get("crit")
            ):
                raise InvalidCredentials
            key = self._get_key(kid)
            if key.algorithm_name != algorithm:
                raise InvalidCredentials
            claims = jwt.decode(
                token,
                key,
                algorithms=list(ALGORITHMS),
                issuer=self.issuer,
                audience="authenticated",
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
            # NumericDate claims must be actual finite JSON numbers, not strings
            # or booleans that some JWT libraries coerce into integers.
            for name in ("exp", "nbf", "iat"):
                if name in claims and (
                    type(claims[name]) not in (int, float) or not math.isfinite(claims[name])
                ):
                    raise InvalidCredentials
            user_id = UUID(claims["sub"])
            if claims.get("role") != "authenticated":
                raise InsufficientPermissions
            return AuthenticatedUser(id=user_id)
        except (jwt.PyJWTError, ValueError, TypeError, OverflowError, RecursionError):
            raise InvalidCredentials from None

    def _get_key(self, kid: str) -> jwt.PyJWK:
        with self._lock:
            now = monotonic()
            if now < self._expires_at and kid in self._keys:
                return self._keys[kid]
            if now >= self._refresh_after:
                self._refresh_after = now + JWKS_REFRESH_INTERVAL_SECONDS
                self._refresh_failed = True
                # Replace the entire set: removed keys disappear on refresh.
                keys = self._fetch_keys()
                self._keys = keys
                self._expires_at = monotonic() + JWKS_TTL_SECONDS
                self._refresh_failed = False
            if monotonic() >= self._expires_at or self._refresh_failed:
                raise AuthenticationUnavailable
            if kid not in self._keys:
                raise InvalidCredentials
            return self._keys[kid]

    def _fetch_keys(self) -> dict[str, jwt.PyJWK]:
        try:
            with httpx.Client(timeout=5.0, trust_env=False, follow_redirects=False) as client:
                with client.stream("GET", self.jwks_url) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_JWKS_BYTES:
                            raise AuthenticationUnavailable
            document = json.loads(body)
            raw_keys = document["keys"]
            if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= MAX_JWKS_KEYS:
                raise AuthenticationUnavailable
            keys = {}
            for raw in raw_keys:
                if not isinstance(raw, dict):
                    raise AuthenticationUnavailable
                algorithm = raw.get("alg")
                if algorithm not in ALGORITHMS:
                    continue
                kid = raw.get("kid")
                if (
                    not isinstance(kid, str)
                    or not 1 <= len(kid) <= 256
                    or kid in keys
                    or raw.get("use", "sig") != "sig"
                    or not isinstance(raw.get("key_ops", ["verify"]), list)
                    or "verify" not in raw.get("key_ops", ["verify"])
                    or "d" in raw
                    or (
                        algorithm == "ES256" and (raw.get("kty"), raw.get("crv")) != ("EC", "P-256")
                    )
                    or (algorithm == "RS256" and raw.get("kty") != "RSA")
                ):
                    raise AuthenticationUnavailable
                key = jwt.PyJWK.from_dict(raw, algorithm=algorithm)
                if algorithm == "RS256" and key.key.key_size < 2048:
                    raise AuthenticationUnavailable
                keys[kid] = key
            if not keys:
                raise AuthenticationUnavailable
            return keys
        except (
            httpx.HTTPError,
            jwt.PyJWTError,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            AttributeError,
            RecursionError,
            httpx.InvalidURL,
        ):
            raise AuthenticationUnavailable from None


@lru_cache(maxsize=4)
def get_token_verifier(supabase_url: str, jwks_url: str) -> SupabaseTokenVerifier:
    """Share a bounded cache by public configuration only, never by user token."""
    try:
        return SupabaseTokenVerifier(supabase_url, jwks_url)
    except ValueError:
        raise AuthenticationUnavailable from None
