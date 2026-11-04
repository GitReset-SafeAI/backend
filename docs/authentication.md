# Supabase authentication and registration decisions

## Implemented token contract

Protected routes accept `Authorization: Bearer <Supabase access token>`.
`get_current_user` in `app/api/dependencies.py` returns an immutable
`AuthenticatedUser` containing only the verified subject UUID. All four feature
modules can reuse it; report creation uses `require_verified_reporter_id` to
pass that UUID to the repository. Registration and password-login endpoints are implemented in this backend;
the browser Supabase SDK owns session persistence and refresh. The authenticated, on-demand
[NIC classification endpoint](nic-classification.md) only parses an encoded
indicator; it does not verify identity or eligibility. Health remains public.

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
| `SUPABASE_PUBLISHABLE_KEY` | Required for signup/login via the public Auth API; not needed for JWKS |

Legacy anon/service-role settings remain independent and are not used as JWT
verification keys. Real values belong in local ignored `.env` or deployment
secrets; never commit them. Settings remain optional so unrelated tests and
health can run without credentials. Protected requests fail closed without
valid verification configuration. The local `.env` is unchanged.

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

## Web registration and login

The web frontend already calls these endpoints through Vite's `/api` proxy:

- `POST /api/v1/authentication/sign-up`: `{name, email, password, nic, phone_number}`.
  Passwords require at least eight characters, names 1-60 characters, and phones
  the normalized Sri Lankan format `+94` followed by nine digits (first nonzero).
  NIC format accepts nine ASCII digits followed by V/X or twelve ASCII digits.
  This is only a shape check, not identity verification or an eligibility check.
- Signup forwards email/password and `data: {name, phone_number}` to Supabase's
  public `/auth/v1/signup` endpoint using the publishable key. Password hashing,
  account creation, and email confirmation are handled by Supabase Auth.
  Name and phone are saved in Auth user metadata; phone is unverified contact
  data, not a phone-auth credential. Never use editable metadata for permissions.
- NIC is transient input: it is not forwarded, persisted, logged, placed in JWTs,
  or used to derive an account gender. Existing NIC privacy rules still apply.
- Signup returns an empty `204` response, including for an already registered
  address. The frontend shows its generic confirmation notice and returns to
  login. No auto-login is attempted, even if email confirmation is disabled.
- `POST /api/v1/authentication/sign-in`: `{email, password}`. Uses Supabase's
  `/auth/v1/token?grant_type=password` endpoint and returns only `access_token`,
  `refresh_token`, `token_type: "bearer"`, and `expires_in`, with no-store headers.
  The frontend calls `supabase.auth.setSession` to persist/refresh that session.
- Invalid input is redacted and returns `422`; invalid login `401`; unconfirmed
  email `403`; provider rate limits `429`; unavailable/misconfigured Auth `503`.
  Provider messages and response bodies are never exposed. Request-body logging
  and Authorization capture must also stay disabled in deployment tooling.

No new table or migration is needed for signup. Supabase owns `auth.users`.
Existing report migrations are still needed to submit reports after login.
If the hosted project already has an Auth trigger that inserts into
`public.user_profiles` with a required `nic`, apply
`supabase/migrations/20261104000100_allow_signup_without_stored_nic.sql`.
That compatibility migration makes NIC nullable, preserving the backend's
transient-only NIC contract. The profile table and trigger are not defined
by this repository; other required profile fields must also match their inputs.

### Supabase dashboard setup

1. Open the project used by both frontend and backend. Under Authentication,
   enable new-user signups and the Email provider. Keep Confirm email enabled.
   Set a minimum password length of eight; if you add stronger password rules,
   also update the form guidance. This proxy does not yet support CAPTCHA tokens,
   so enabling Supabase CAPTCHA requires extending the form and request contract.
2. Under Authentication > URL Configuration, set the Site URL to
   `http://localhost:5173/login` for local testing. If you use `127.0.0.1`, use
   `http://127.0.0.1:5173/login` instead. Set the deployed login URL in production.
   Signup uses the configured Site URL as the email confirmation destination;
   the user confirms via the email link, then logs in with their password.
   Use Supabase's standard `{{ .ConfirmationURL }}` signup email link; a custom
   PKCE/token-hash template needs a separate callback implementation.
3. Configure custom SMTP for signup emails to ordinary users. Supabase's default
   sender is restricted to project-team recipients and has tight rate limits.
   For initial testing without SMTP, use a project-team email address.
4. Copy the project URL and **publishable** key from the project's API settings.
   Backend `.env` needs `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`.
   Frontend `.env`/`.env.local` needs `VITE_AUTH_MODE=supabase`,
   `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`, and
   `VITE_API_BASE_URL=http://127.0.0.1:8001`. Both must reference the same project.
   Never put secret/service-role keys in any `VITE_` variable.
5. For protected backend routes, configure `SUPABASE_JWKS_URL` as
   `https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json` and check
   Authentication > Signing Keys uses ES256 or RS256. The existing verifier
   rejects legacy HS256 tokens. Privileged report storage separately requires
   `SUPABASE_SECRET_KEY` and the existing report migrations.
6. Restart both development servers after environment changes:

   ```powershell
   # In backend
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8001
   # In frontend-web, in a second terminal
   npm.cmd run dev
   ```

7. Open `/login?mode=signup`, register with an email you control, check
   Authentication > Users for the account, follow its confirmation email, and
   log in. Name and phone appear in the user's metadata; NIC must not appear.
   Verify a protected request succeeds using the new session. With confirmations
   enabled, login before confirming should show the confirmation error.

References: [Password authentication](https://supabase.com/docs/guides/auth/passwords),
[SMTP setup](https://supabase.com/docs/guides/auth/auth-smtp),
[Auth configuration](https://supabase.com/docs/guides/auth/general-configuration),
[User metadata](https://supabase.com/docs/guides/auth/managing-user-data).

### Diagnosing signup 503 responses

The browser receives a generic failure; the backend emits a sanitized
`Supabase Auth ...` warning with the upstream HTTP status and an allowlisted
error code. It does not log submitted values or provider error messages.

- `transport failure`: check outbound connectivity, DNS, TLS, and firewall rules
  on the backend host. Requests have a ten-second timeout and do not use
  environment proxy settings.
- `invalid URL or missing publishable key`: check backend `.env` and restart
  Uvicorn; settings are cached per process.
- `status=500 code=unexpected_failure`: inspect the matching signup event in
  Supabase Authentication logs for email-delivery, database-trigger, or Auth-hook
  errors. The generic code alone cannot distinguish them.
- `email_address_not_authorized`: configure custom SMTP or use a project-team
  recipient for testing with Supabase's default sender.
- `status=401` / `403` with `code=unknown`: verify the publishable key belongs
  to the configured project and has not been revoked.

Do not paste credentials, raw request bodies, or full Auth logs containing
personal data into diagnostics.

## Offline verification

`tests/test_auth.py` generates fresh ES256/RS256 keys in memory and exercises
actual signatures and claims through protected report requests. HTTPX transports
simulate JWKS rotation, outages, and Data API results; no real tokens or
credentials are used. Tests isolate environment loading and clear verifier
caches. Existing repository tests additionally cover submitted/saved data
integrity. These checks do not establish live Supabase compatibility.

`tests/test_registration.py` additionally checks registration/login contracts,
input redaction, metadata forwarding without NIC, duplicate handling, provider
errors, timeouts, and invalid configuration with mocked HTTP only.
