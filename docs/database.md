# Reporting database

## Initial migration

`supabase/migrations/20261023085827_create_reports.sql` creates only
`public.reports`. It expects an existing Supabase database with `auth.users` and
the `anon` and `authenticated` roles. No Auth objects, other feature tables,
extensions, or seed records are created or modified.

The directory previously contained only `.gitkeep`, so this migration establishes
the standard Supabase `<UTC timestamp>_<description>.sql` naming convention.
Apply migrations through the team's migration workflow; application startup does
not apply them. Once applied, make later changes in new migration files.

The table contains:

- A UUID primary key with `gen_random_uuid()` as its database default.
- Required `user_id`, referencing `auth.users(id) on delete restrict`, and a
  `reports_user_id_idx` index for ownership queries.
- Required original and translated text, each with a trimmed length of 1–5000
  characters. The checks trim POSIX whitespace for validation only; stored text
  is preserved. Every INSERT includes English text in `translated_text`; the
  reporting service copies English originals or prepares a translation first.
- Required `input_method` and `language_hint`, constrained to the agreed values
  in `app/schemas/report.py`.
- Optional latitude and longitude within their agreed ranges, required together.
- Optional `occurred_at` and required `submitted_at`, both `timestamptz`, with
  `submitted_at` defaulting to `now()`.

Future backend inserts should omit `id` and `submitted_at` to use the database
defaults and return the saved values. Defaults do not prevent a privileged SQL
caller from explicitly supplying values; the eventual write path must enforce
the client/server field boundary.

PostgreSQL `timestamptz` stores an instant; its display depends on the session
timezone. It does not retain the original timezone or reject every offset-free
input. Keep timezone-awareness validation in the existing Pydantic schemas;
`ReportResponse` already normalises submission times to UTC.

The requested database text check measures trimmed length. The existing
Pydantic schemas measure the supplied length and separately reject blank text.
Consequently, direct SQL can accept padded text longer than 5000 characters if
its trimmed length fits. The schemas are unchanged; the future reporting write
path must continue validating through them.

## Context migration and creation repository

`20261023140715_add_report_context.sql` adds nullable text columns for
`lighting_condition`, `crowd_level`, and `current_safety_status`, with CHECK
constraints matching their shared Pydantic enums. Existing rows acquire null
values. The original migration is unchanged, including `ON DELETE RESTRICT`.

The new migration also resets table privileges for `service_role` to SELECT and
INSERT, needed for creation with a returned representation. It adds no policies
and grants no access to `PUBLIC`, `anon`, or `authenticated`. RLS remains enabled.
No table UPDATE or DELETE privileges are added for the creation path.

`app/repositories/reports.py` implements a small server-side Supabase Data API
adapter using the existing HTTPX dependency. HTTPX is now declared as a runtime
dependency; no new package or database architecture is introduced. It uses HTTPS
and `SUPABASE_SECRET_KEY` in the `apikey` header, with a ten-second timeout and
no automatic creation retries. It does not initialise an Auth client or attach
user tokens to the privileged client. The optional legacy settings remain
unchanged but are not a fallback for this modern-key transport.

The repository sends all ten prepared submission fields plus the trusted `user_id` in a
single INSERT, omitting `id` and `submitted_at`. It requests the created row via
`Prefer: return=representation`, including all twelve response fields and the
owner for an internal consistency check. The returned row must be complete,
belong to the expected owner, and retain every submitted value. Only then is it
validated and returned through `ReportResponse`, which excludes `user_id` and
normalises submission time to UTC. Missing or malformed provider results produce
a generic persistence failure. A failed response is not proof the INSERT was
rolled back; no idempotency or retry mechanism is implemented here.

Optional omitted and explicitly null values are stored as SQL NULL. Explicit
unknown/prefer-not-to-say enum choices remain their actual string values. No
incident classification, inferred safety status, journey history, or extra
location data is stored.

Translation adds no columns, grants, or migrations. `ReportCreateRequest` permits
omitting a translation, but the reporting service builds a strict `ReportSubmission`
before calling this repository. A required translation failure prevents the INSERT;
there is no pending/untranslated database row. Legacy supplied translations for
non-English hints remain accepted without independent verification.

`ReportRepository.get_owned_translation` uses the existing SELECT privilege for
analysis input. It selects only `id`, `user_id`, and `translated_text`, filters by
both report ID and verified owner ID, and validates returned identity and nonblank
text. This is an internal read, not a new retrieval API. It does not require
UPDATE/DELETE privileges or change RLS, client grants, or `ON DELETE RESTRICT`.

See [Supabase's Data API](https://supabase.com/docs/guides/api) and
[API key guidance](https://supabase.com/docs/guides/getting-started/api-keys).

## Verified authentication and remaining project setup

`get_current_user` verifies asymmetric Supabase access tokens and
`require_verified_reporter_id` supplies their UUID subject before opening the
privileged client. Verification uses public JWKS, separately from database
credentials. Missing/invalid authentication returns 401; unavailable verification
configuration/keys returns 503. No fake user, anonymous-owner default, or
client-supplied owner ID is accepted. See [authentication](authentication.md)
for supported algorithms, role permissions, cache behaviour, and NIC decisions.

Direct client reads and writes are intentionally unavailable. RLS without
policies denies row access to ordinary roles, while table grants are a separate
permission layer. Table owners, superusers, and roles with `BYPASSRLS` may bypass
row policies. RLS does not provide ownership isolation for the privileged
creation client.
See the [Supabase RLS reference](https://supabase.com/docs/guides/database/postgres/row-level-security).

Creation binds `user_id` to the verified backend identity; future
owner-facing reads or updates must also filter by this verified ID. Apply both
migrations to an agreed Supabase test project through the team's workflow and
verify its actual permissions and asymmetric Auth signing configuration before
using it. No migration is applied on application startup, and none has been
applied remotely by this task.

Keep service-role credentials and third-party keys on the backend. Privileged
service-role requests can bypass RLS, so enabling RLS does not replace backend
authorisation. `user_id` remains private database ownership data and is not
added to `ReportSubmission` or public responses. Reports remain internally
linkable to their users; this does not provide technical anonymity.

## Account deletion

`ON DELETE RESTRICT` prevents deleting a referenced Auth user while their reports
exist. Reports are not automatically deleted when account deletion is attempted.
Account deletion, retention, and possible anonymisation need a separate team
decision and implementation. No account-deletion workflow is included here.

## Verification

The migration was applied successfully to an isolated PostgreSQL 17.5 cluster
using test-only fixtures for `auth.users` and Supabase roles. SQL checks covered
column types, generated defaults, primary and foreign keys, restricted user
deletion, the ownership index, text boundaries, enum values, coordinate pairing
and ranges (including non-finite values), and timestamp instants.

Access checks started with permissive default grants and verified that the
migration removed client privileges. A separate ordinary test role with table
grants confirmed that no-policy RLS still denied row access and writes. These
test-only grants were rolled back. A privileged test role demonstrated RLS
bypass separately. The disposable cluster was stopped after verification.

This validates the migration on PostgreSQL with simulated Auth prerequisites;
it is not a complete Supabase integration test. No existing local database,
Supabase project, or managed Auth object was changed. The existing pytest suite
and Ruff lint/format checks also passed.

The normal pytest suite now exercises the creation endpoint and repository with
`httpx.MockTransport`, dummy settings, synthetic identities, and synthetic rows.
It requires no Supabase credentials and makes no external requests. Successful
repository-focused creation tests override the verified identity dependency.
Separate authentication tests generate signing keys and exercise signature and
claim verification through the endpoint with controlled JWKS responses. Neither
test group proves live Supabase authentication or persistence.

`tests/sql/test_report_creation.sql` separately applies both migrations and
checks context fields, defaults, existing-row preservation, ownership constraints,
and runtime privileges using test-only Auth fixtures. It refuses databases that
are not loopback databases named `saferoute_report_test_<timestamp>` or already
contain `auth.users`. Run it only in a new disposable PostgreSQL database using
`psql --set=ON_ERROR_STOP=1 --file=tests/sql/test_report_creation.sql`. It does not
run in the normal pytest suite. This SQL check is distinct from a full Supabase
or application-to-database integration test.

For this creation change, both migrations and the SQL script passed on PostgreSQL
17.5 in a fresh isolated local database. The focused report/schema suite passed
148 tests and the complete pytest suite passed 164 tests. Ruff lint and formatting
checks passed. The isolated server was stopped afterward; no remote database was
accessed or modified.

The subsequent authentication change passed 230 focused authentication,
report-endpoint, and schema tests, and 246 tests in the complete pytest suite.
Ruff lint, formatting, and Git whitespace checks passed. The existing
Starlette/HTTPX TestClient deprecation warning remains. No SQL schema changed,
and the local SQL checks were not rerun for authentication. No live Supabase
Auth, RLS, registration, or persistence verification was performed.
