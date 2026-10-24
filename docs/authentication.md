# Supabase authentication and registration decisions

## Implemented token contract

Protected routes accept `Authorization: Bearer <Supabase access token>`.
`get_current_user` in `app/api/dependencies.py` returns an immutable
`AuthenticatedUser` containing only the verified subject UUID. All four feature
modules can reuse it; report creation uses `require_verified_reporter_id` to
pass that UUID to the repository. No login, registration, refresh-token, or NIC
endpoint is implemented in this backend. Health remains public.

The repository did not establish the project's active signing algorithm.
Configuring a JWKS URL alone does not establish asymmetric signing. Without
accessing the real project or its credentials, this implementation deliberately
supports **only ES256 (P-256) and RS256 (RSA >= 2048 bits)**. Confirm the active
signing key in the Supabase dashboard before deployment. Legacy HS256 and
unsigned tokens are rejected; no symmetric JWT secret or fallback was added.
If the project still uses HS256, agreeing on a signing-key transition or a
separate Auth-server verification integration remains a deployment blocker.
See [Supabase signing keys](https://supabase.com/docs/guides/auth/signing-keys)
and [Supabase JWT verification](https://supabase.com/docs/guides/auth/jwts).

Verification uses PyJWT with its cryptography extra, a fixed algorithm allowlist,
and public keys retrieved only from the configured trusted endpoint. It requires:

- A valid signature with a matching `kid` and JWK algorithm/key type.
- Exact issuer `${SUPABASE_URL}/auth/v1` and audience `authenticated` (or an
  audience array containing that value).
- A required, unexpired `exp` and non-empty UUID `sub`. Optional `nbf` and `iat`
  are checked when present; NumericDate values must be finite JSON numbers.
- The verified `role` must be `authenticated` for protected user operations.

No clock-skew allowance is configured; keep the server clock accurate. Tokens
are limited to 16 KiB, key IDs to 256 characters, and unsupported critical header
extensions are rejected. Token-supplied `jku`/`x5u` URLs are never followed.
Unverified payloads never supply identities. The verifier neither retains nor
returns other claims, account metadata, or the access token.

## Configuration and signing-key lifecycle

Existing settings are reused; no new environment variables are required:

| Setting | Purpose |
|---|---|
| `SUPABASE_URL` | Trusted HTTPS project origin, without a path, credentials, query, or fragment |
| `SUPABASE_JWKS_URL` | Exactly `${SUPABASE_URL}/auth/v1/.well-known/jwks.json` |
| `SUPABASE_SECRET_KEY` | Backend-only privileged Data API access; never a JWT verification key |
| `SUPABASE_PUBLISHABLE_KEY` | Existing optional client configuration; not needed for public JWKS retrieval |

Legacy anon/service-role settings remain independent and are not used as JWT
verification keys. Real values belong in local ignored `.env` or deployment
secrets; never commit them. Settings remain optional so unrelated tests and
health can run without credentials. Protected requests fail closed without
valid verification configuration. `.env.example` and local `.env` are unchanged.

The synchronous dependency runs in FastAPI's thread pool. Each process caches
at most four verifier configurations, each with at most 32 keys for 10 minutes.
A lock serialises refreshes; at most one JWKS fetch is attempted every 30 seconds
per configuration, including failed fetches and unknown-key requests. An
unknown key triggers refresh only when this cooldown permits; a newly rotated
key may be rejected temporarily during that interval. A successful refresh
replaces the complete key set. Expired cache entries are never accepted during
an outage; still-current known keys remain usable until expiry.

JWKS requests use five-second HTTPX timeouts, a 128 KiB response limit, no
redirects, and no environment proxy settings. They carry no API key or user
token. Empty, malformed, oversized, or unusable key sets fail closed. The
Supabase edge also caches JWKS; coordinate key publication and revocation with
its documented propagation delays. Restart workers to clear application caches
when necessary. Local verification does not query account/session revocation
on every request: a signed access token can remain valid until expiry, subject
to signing-key cache propagation. Define short token lifetimes and account
disablement requirements before production use.

## Errors and database ownership

Missing/malformed credentials, signature/claim failures, unsupported algorithms,
and keys absent from a successfully retrieved set return `401` with
`WWW-Authenticate: Bearer` and a generic detail. A verified token without the
authenticated-user role returns `403`. Missing/invalid configuration or
unavailable trusted keys returns `503` with `Authentication is unavailable.`
These errors occur before a privileged storage client is opened. Tokens, claims,
NIC data, report descriptions, and provider error bodies are not logged by this
implementation. Configure deployment request tracing to redact Authorization
headers and request bodies as well.

Token verification does **not** make database requests user-scoped. The existing
Data API client separately sends the backend secret in `apikey`, bypassing RLS.
Creation explicitly inserts the verified UUID as `user_id` and checks the saved
owner before returning success. Client owner fields are rejected by the existing
schema; owner headers have no effect. No user token is forwarded to this client.
Future private queries must filter by the verified owner. RLS, grants, report
schemas, and `auth.users(id) ON DELETE RESTRICT` are unchanged.

Before using the real test project, the team must confirm its signing mode,
issuer/audience contract, JWKS endpoint, Auth registration settings, and applied
report migrations/grants. No live project or remote migration was accessed by
this task, so live Auth, RLS, and persistence remain unverified.

## NIC registration is a separate, unresolved integration

No existing registration screen/backend contract, NIC provider, verification
process, or approved NIC storage was found. Supabase Auth manages accounts;
the mobile registration methods and Supabase project signup settings still need
team agreement. Successful JWT verification establishes the Supabase account's
identity, not a person's NIC, gender, or legal identity.

**Current policy:** report submission requires a verified Supabase account token
and does not require NIC collection or verification. No NIC fields, storage,
format validator, derived identifier, or simulated verification were added.
Do not put NIC values in Supabase Auth user/app metadata or custom JWT claims,
tokens, report records, public APIs, logs, or hotspot datasets.

If a separately approved registration flow needs format checks, it should
recognise the older nine ASCII digits followed by `V`/`X` (case-insensitive) and
the newer twelve ASCII digits. The [Department for Registration of Persons
FAQ](https://drp.gov.lk/en/faq.php) describes the change between these formats.
Matching either shape is **not** proof of issuance, authenticity, or ownership.
Do not infer eligibility, gender, or verification from the number's structure.

Before collecting any NIC, agree on its necessary purpose, whether it is
optional, a trusted provider or reviewer process, user notices/consent, and how
users without verification can register/report. Prefer a provider reference and
restricted verification outcome over retaining the full number. If full retention
is explicitly justified, approve a separate private identity store, encryption
and key management, narrowly authorised access with audited/redacted events,
retention/deletion deadlines, and disclosure controls first. None exists yet.
Plain unsalted hashes do not anonymise predictable NIC numbers and must not be
used. Do not collect or retain full NICs while these decisions remain unresolved.

## Offline verification

`tests/test_auth.py` generates fresh ES256/RS256 keys in memory and exercises
actual signatures and claims through protected report requests. HTTPX transports
simulate JWKS rotation, outages, and Data API results; no real tokens or
credentials are used. Tests isolate environment loading and clear verifier
caches. Existing repository tests additionally cover submitted/saved data
integrity. These checks do not establish live Supabase compatibility.
