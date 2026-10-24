# API contracts

The running development application exposes Swagger UI at `/docs` and its OpenAPI
schema at `/openapi.json`. Versioned endpoints use the `/api/v1` prefix.

`app/main.py` mounts the central `app/api/router.py`, which registers health and
the `reporting`, `incident_ai`, `integrity`, and `safety_map` feature routers.
Feature routers live in `app/modules/<feature>/router.py` and currently expose
no endpoints. Their URL paths, shared schemas, and inter-module interfaces remain
undecided; the module structure does not define new API contracts.

## Current endpoint

`GET /api/v1/health` requires no credentials and returns HTTP 200:

```json
{"status": "ok", "service": "saferoute-ai-backend", "version": "0.1.0"}
```

The version comes from `APP_VERSION`. This endpoint confirms process health only;
it does not check Supabase or an LLM service.

## Report creation: POST /api/v1/reports

The route accepts `ReportSubmission` and returns `ReportResponse`. Send
`Authorization: Bearer <Supabase access token>`. The backend verifies ES256/RS256
signatures against the configured project JWKS, the exact project issuer,
`authenticated` audience/role, expiry, and UUID subject before storage access.
The verified subject supplies report ownership. Anonymous submission and
client-supplied owner identities are not supported. NIC collection or verification
is not a prerequisite. See [authentication and registration decisions](authentication.md)
for configuration, supported signing mode, key rotation, and remaining live setup.

| Request field | Required | Validation |
|---|---|---|
| `original_text` | Yes | Nonblank string, 1–5000 supplied characters |
| `translated_text` | Yes | Nonblank string, 1–5000 supplied characters; English text must also be supplied here |
| `input_method` | Yes | `text` or `voice` |
| `language_hint` | Yes | `si`, `si-Latn`, `en`, `ta`, `mixed`, or `unknown` |
| `latitude`, `longitude` | No | Both values or both null/omitted; latitude −90–90, longitude −180–180 |
| `occurred_at` | No | Timezone-aware timestamp or null; no inferred occurrence time |
| `lighting_condition`, `crowd_level`, `current_safety_status` | No | Nullable enums listed below |

Unknown fields are rejected, including `user_id`, `id`, and `submitted_at`.
The backend derives ownership from the verified dependency; the database
generates the report ID and submission timestamp. The optional-context example
below is a valid request. All three context fields can be removed for a minimal
request. `voice` means the payload already contains final report text; this
endpoint does not accept audio or perform transcription or translation.

A successful authenticated insert returns HTTP 201 with this synthetic example body:

```json
{
  "id": "4bf9b65c-9451-40e0-a40c-e26376a3a5d9",
  "original_text": "Synthetic demo: someone followed me after I left the bus.",
  "translated_text": "Synthetic demo: someone followed me after I left the bus.",
  "input_method": "text",
  "language_hint": "en",
  "latitude": null,
  "longitude": null,
  "occurred_at": null,
  "lighting_condition": "PARTIALLY_LIT",
  "crowd_level": "ALMOST_EMPTY",
  "current_safety_status": "MAY_STILL_BE_IN_DANGER",
  "submitted_at": "2026-10-23T14:00:00Z"
}
```

The response excludes the private database `user_id`. No list, retrieval, edit,
delete, or moderation endpoint is provided.

| Status | Meaning |
|---|---|
| `201` | Data API returned exactly one complete saved row with the expected owner and submitted values, validated through `ReportResponse` |
| `401` | Missing/malformed bearer credentials or invalid/unsupported token; includes `WWW-Authenticate: Bearer` |
| `403` | Verified token lacks the authenticated-user role |
| `422` | Request validation failed; `detail` is a list of `loc`, `msg`, and `type`, without submitted input values or validation context |
| `503` | Verification configuration/trusted keys are unavailable, storage configuration is unavailable, the Data API failed/timed out, or its saved-row response was incomplete/invalid |

Authentication can pre-empt schema validation. Missing or invalid credentials return:

```json
{"detail": "Invalid or missing authentication credentials."}
```

Verification infrastructure failures use `{"detail": "Authentication is unavailable."}`.
Storage failures use `{"detail": "Report storage is unavailable."}`. Provider
errors, credentials, SQL details, and stack traces are not returned. A timeout or
invalid response can occur after a database commit; the backend does not claim
the report was unsaved and does not automatically retry creation. Idempotency is
not implemented.

## Adding endpoints

Agree on each contract before feature implementation. Record the HTTP method,
path, purpose, authentication requirements, Pydantic request and response schemas,
required and optional fields, validation rules, status codes, and error examples.
Specify pagination, timestamps/time zones, and coordinate conventions where relevant.
Use synthetic examples without personal data. Keep OpenAPI and contract tests
consistent, and coordinate breaking changes with the mobile team.

Classification, severity, anomaly detection, hotspots, and route recommendations
have no endpoints implemented yet. Inter-module contracts remain to be agreed.

## Optional report context

`app/schemas/report.py` defines `ReportSubmission` and `ReportResponse`. The
following context fields are independently optional and nullable in both models.
Omitting them preserves compatibility with existing report payloads. Omitted
fields default to `null` in normal model serialisation; explicitly choosing an
unknown/prefer-not-to-say value preserves that enum value rather than replacing
it with `null`.

| Field | Allowed values, in addition to `null` |
|---|---|
| `lighting_condition` | `WELL_LIT`, `PARTIALLY_LIT`, `POORLY_LIT_OR_DARK`, `DAYLIGHT`, `UNKNOWN_OR_PREFER_NOT_TO_SAY` |
| `crowd_level` | `ALMOST_EMPTY`, `NOT_VERY_CROWDED`, `MODERATELY_CROWDED`, `VERY_CROWDED`, `UNKNOWN_OR_PREFER_NOT_TO_SAY` |
| `current_safety_status` | `SAFE_NOW`, `UNSURE`, `MAY_STILL_BE_IN_DANGER`, `PREFER_NOT_TO_SAY` |

Values are case-sensitive. Invalid values raise the same Pydantic validation
errors as the existing enums. The other report validation rules are unchanged.

Synthetic example of a valid submission model, with all three fields supplied:

```json
{
  "original_text": "Synthetic demo: someone followed me after I left the bus.",
  "translated_text": "Synthetic demo: someone followed me after I left the bus.",
  "input_method": "text",
  "language_hint": "en",
  "lighting_condition": "PARTIALLY_LIT",
  "crowd_level": "ALMOST_EMPTY",
  "current_safety_status": "MAY_STILL_BE_IN_DANGER"
}
```

Removing any or all of the three context fields, or setting them to `null`, is
also valid. `ReportResponse` inherits the optional fields and serialises enum
values as the strings above in JSON, while retaining its required server-supplied
`id` and `submitted_at` fields.

These values are private, self-reported context. Current safety describes the
user's stated situation when reporting and is independent of AI classification
or severity. Selecting `MAY_STILL_BE_IN_DANGER` does not contact emergency
services, dispatch assistance, send notifications, or enable location tracking.
The fields are not approved for public map responses.

The creation repository sends every supported schema field, including these
nullable fields, to the Data API. Omission and explicit null both become SQL
NULL; an explicit enum choice stays distinct from null. The context migration
must be applied before storage can support these fields. Report creation requires
verified authentication and configured storage. The health endpoint is unchanged.
