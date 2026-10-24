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

Classification, severity and anomaly detection
have no endpoints implemented yet. The safety map endpoints are implemented
and documented below.

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

Reporting, classification, severity, anomaly detection, hotspots, and route

## Safety map (Module 4) — provisional

Agreed shapes only; neither endpoint is implemented. The mobile mirror lives in
`src/services/contracts/safetyMap.ts` and the Pydantic models in
`app/modules/safety_map/schemas.py`. Change both sides together.

Conventions for these endpoints:

- Coordinates are always `{"latitude": ..., "longitude": ...}` objects. GeoJSON
  `[longitude, latitude]` ordering is converted inside PostGIS and routing
  adapters and never crosses the API boundary.
- Timestamps are UTC ISO-8601.
- No report text, identifiers, timestamps, or precise coordinates are returned.
  Only aggregates leave the backend.
- A hotspot requires at least `MIN_REPORTS_PER_HOTSPOT` (3) reports and a radius
  of at least 150 m. Clusters below that are withheld and counted in
  `suppressed_area_count`.
- `evidence_level` states how well the reports support what is shown. An area
  with no hotspots is `insufficient` evidence, not a safe area.

### `GET /api/v1/safety-map/hotspots`

Query parameters: `bbox` (`min_lon,min_lat,max_lon,max_lat`, required),
`time_bucket` (`day|evening|night|all`, default `all`), `window_days`
(default 30).

Returns `HotspotsResponse`. Synthetic example:

```json
{
  "generated_at": "2026-10-23T18:00:00Z",
  "window_days": 30,
  "min_reports_per_hotspot": 3,
  "suppressed_area_count": 4,
  "evidence_level": "limited",
  "hotspots": [
    {
      "id": "demo-hotspot-1",
      "center": {"latitude": 6.8905, "longitude": 79.8565},
      "radius_m": 180,
      "report_count": 9,
      "intensity": 0.82,
      "top_incident_categories": ["UNWANTED_FOLLOWING", "VERBAL_SEXUAL_HARASSMENT"],
      "top_safety_concerns": ["POOR_STREET_LIGHTING"],
      "peak_time_bucket": "evening",
      "most_recent_report_days_ago": 2,
      "mean_confidence": 0.88,
      "evidence_level": "moderate"
    }
  ]
}
```

### `POST /api/v1/safety-map/routes/compare`

Route planning is outside the required initial scope. The contract is recorded
so it can be added without changing the hotspot response.

Accepts `RouteComparisonRequest` and returns `RouteComparisonResponse`: a list
of candidate walking routes plus `recommended_route_id`.

Every route carries its own `exposure_score`, `hotspots_passed`,
`incident_categories`, `safety_concerns`, and `extra_duration_s` relative to the
fastest route, so the
app can explain each path rather than only rank them. `exposure_score` is a
relative indicator of proximity to reported-incident areas and must never be
presented as a safety guarantee.

`recommended_route_id` is `null` when no candidate meaningfully lowers exposure
within an acceptable detour. The agreed rule, applied server-side and mirrored
in the mobile mock: recommend only when exposure drops by at least 25% and the
detour adds no more than `max(300 s, 30% of the fastest route)`.

### Incident-AI alignment

Categories follow `incident_ai_analyses` on the `incident_understanding`
branch. `IncidentCategory` and `SafetyConcernCategory` are carried as separate
fields throughout, because `OTHER` and `UNCLEAR` appear in both enums and would
be ambiguous in one list. Unrecognised values are passed through and rendered
with a neutral label rather than dropped, so adding a category there cannot
break the map.

`severity` is a smallint 0-10, so hotspot weighting uses `severity / 10`.

**Eligibility (agreed).** Only analyses with `analysis_status = 'completed'`
contribute to hotspots or routing. A report the AI could not read is not
evidence and waits for human review instead of appearing on the map.

**Confidence (agreed).** `confidence` weights a report's contribution rather
than excluding it: hotspot intensity and route exposure are both scaled by it,
so an area the AI understood poorly moves a route recommendation less than one
it read clearly. There is no hard confidence cut-off, because a genuine report
written in mixed Sinhala and English can score low without being false. Each
hotspot publishes `mean_confidence` so the map can say how well the underlying
reports were understood.

### Reports and location

`incident_ai_analyses` has no coordinates, so
`20261023000400_create_incident_reports.sql` proposes the reports table that
`report_id` points at, with a PostGIS `geography(Point, 4326)` column.

It is written for reporting rather than only for the map: the report body,
input method, report language, the optional time, location and context
answers, and the integrity columns. Everything except the body is optional,
because a report must not be rejected for unanswered optional questions. A
report with no location is analysed normally; it simply cannot contribute to an
area. Context columns reuse the `incident_ai_analyses` vocabularies, so a value
a reporter gave and a value the AI inferred are directly comparable, and the
view prefers the reporter's answer where both exist.

No foreign key is declared between the two tables yet, so the modules can be
developed and seeded independently. Add one once both write through the same
path. The reporting module owns this table and should extend it rather than
replace it.

`supabase/seed.sql` loads the same synthetic dataset into those tables, marked
`is_synthetic` so demo rows can always be told apart and removed. Regenerate
both it and the JSON with
`python sample-data/generate_synthetic_reports.py`.

### Open items

1. **Integrity.** Duplicate and anomaly flags come from the integrity module
   and are not yet available. Every seeded report currently counts once.

### Running the safety map

Both endpoints are implemented. With no Supabase credentials the service reads
the committed synthetic dataset, so the map is demonstrable without a database:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
curl "http://127.0.0.1:8000/api/v1/safety-map/hotspots?bbox=79.80,6.80,79.95,6.96"
```

Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` and the same service reads
`map_eligible_incidents` instead. Nothing else changes.

Walking routes come from the public OSRM demo server. When it cannot be reached
the response still returns routes, with `uses_street_network: false` so the app
can say they are approximate rather than presenting drawn lines as directions.
