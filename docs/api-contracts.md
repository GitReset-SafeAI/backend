# API contracts

The running development application exposes Swagger UI at `/docs` and its OpenAPI
schema at `/openapi.json`. Versioned endpoints use the `/api/v1` prefix.

`app/main.py` mounts the central `app/api/router.py`, which registers health and
the `reporting`, `incident_ai`, `integrity`, and `safety_map` feature routers.
Feature routers live in `app/modules/<feature>/router.py`. The reporting creation
route lives in `app/api/routes/reports.py`; its translation business logic lives
in the reporting module. Relevant contracts are described below.

## Current endpoint

`GET /api/v1/health` requires no credentials and returns HTTP 200:

```json
{"status": "ok", "service": "saferoute-ai-backend", "version": "0.1.0"}
```

The version comes from `APP_VERSION`. This endpoint confirms process health only;
it does not check Supabase or an LLM service.

## Local NIC classification: POST /api/v1/authentication/nic-classification

Requires the existing verified Supabase bearer token. Accepts `{"nic": "<number>"}`
and returns only `gender_indicator` (`male|female`), `nic_format` (`old|new`),
`classification_method: "nic_format"`, and `identity_verified: false` with HTTP 200.
The NIC is processed transiently, never returned or persisted. Invalid input uses
the shared redacted 422 detail-list response; authentication uses the existing
401/403/503 responses. This does not establish identity, actual gender, age, or
eligibility and is not required for reporting. See
[NIC classification](nic-classification.md) for synthetic examples and date rules.

## Draft voice transcription: POST /api/v1/reporting/transcriptions

This authenticated endpoint returns an editable draft only. It never creates a
report or calls persistence, text translation, or incident classification. Reuse
the normal Supabase bearer access token; no database secret is needed for this
operation. JWT verification happens before the handler reads any audio bytes.

Request:

```http
POST /api/v1/reporting/transcriptions?language_hint=si
Authorization: Bearer <Supabase access token>
Content-Type: audio/wav

<raw binary PCM WAV bytes>
```

There is no JSON, multipart form, filename, audio URL, or account-ID input.
`language_hint` accepts the shared `en`, `si`, `ta`, `si-Latn`, `mixed`, and
`unknown` values and defaults to `unknown` when omitted.

The request body remains raw bytes. Accepted `Content-Type` values are:

| Media type | Accepted content |
|---|---|
| `audio/wav` | RIFF/WAVE PCM encoding tag 1, 16-bit samples, mono/stereo, 8–48 kHz |
| `audio/mp4`, `audio/x-m4a` | M4A/MP4 container with an AAC audio stream, mono/stereo, 8–48 kHz |
| `video/mp4` | MP4 containing an AAC audio stream; only the first audio stream is decoded |
| `audio/mpeg` | MP3 audio, mono/stereo, 8–48 kHz |

Use `audio/mpeg` for MP3, not `audio/mp3`. WebM, Ogg, FLAC, raw AAC, multipart,
and JSON uploads are not supported. MIME types are declarations, not proof:
FFprobe parses compressed inputs with restricted demuxers; invalid or
mislabelled bytes fail validation. MP4 without audio is rejected. A container
may include video, but no video frames are decoded or sent to Groq. The compressed
upload temporarily includes any video bytes it contains; prefer audio-only input.

All formats must satisfy 10485760 bytes (10 MiB) and 60 seconds of selected audio.
The existing WAV path needs no FFmpeg and retains its validation. Floating-point,
compressed WAV, WAVE_FORMAT_EXTENSIBLE, and other WAV sample formats remain
unsupported. RIFF/chunk lengths, PCM byte rate, frame alignment,
actual frame data, and duration are checked. Empty frames, truncated/inconsistent
files, and digital silence are rejected. Digital silence detection does not prove
that other audio contains speech; noise and background sounds can still confuse ASR.

An incremental read checks the actual number of bytes, including uploads without
`Content-Length` or with a falsely small value. A declared oversized length is
rejected before reading. A supplied length that disagrees with the actual body is
invalid. The application buffers only up to its byte limit; this does not impose
a transport/proxy-level memory bound on individual ASGI receive messages. The
upload deadline is 30 seconds. Two requests per process may occupy transcription
slots, including upload, probing, conversion, and provider work; additional requests fail
immediately with 503. Slots are released on failure, timeout, or cancellation.
Multiple workers have separate limits; this is not a distributed or per-user quota.

Compressed uploads require FFmpeg and FFprobe on the backend's trusted `PATH`.
See [runtime setup](../README.md#audio-conversion-runtime). Probing checks the first
audio stream, codec, channels, sample rate, and its duration when available.
MP4 probing reads container metadata without stream-info frame decoding; only
AAC/MP3 decoders are allowed in the subsequent audio conversion process.
Decode output is independently bounded to 16000 mono samples/second, 16-bit PCM,
with at most `floor(max_duration * 16000) * 2` PCM bytes (1,920,000 at 60 seconds).
Excess output causes rejection, never a silently truncated draft. FFmpeg also
stops at the duration limit plus one second; the extra second is a rejection
sentinel, not accepted audio. Probe JSON is capped at 16384 bytes. Each process
uses one codec/filter thread and a 16 MiB individual-allocation limit; this is
not a total process-memory sandbox. Network protocols and MOV external track
references are disabled. Executables, arguments, and paths are backend controlled.

One deadline covers probing and decoding. On timeout/cancellation the child is
killed and reaped and its private temporary directory removed before capacity is
released. Fixed audio mapping skips video, subtitles, and data. Only normalized
16 kHz mono PCM WAV and necessary parameters reach the unchanged Groq adapter.
Existing WAV requests retain their valid sample rate/channel count and have
unrelated metadata stripped. No Python media dependency or SDK is added.
Credentials/base URL come
from `GROQ_API_KEY` and `LLM_BASE_URL`; the independent settings are:

| Setting | Default | Validation |
|---|---|---|
| `TRANSCRIPTION_MODEL` | `whisper-large-v3` | Nonempty; whitespace-only values fail safely when used |
| `TRANSCRIPTION_TIMEOUT_SECONDS` | `30` | Positive, finite seconds; bounds the complete provider operation |
| `TRANSCRIPTION_CONVERSION_TIMEOUT_SECONDS` | `15` | Positive, finite seconds up to 30; bounds probing plus decoding |
| `TRANSCRIPTION_MAX_BYTES` | `10485760` | Positive integer up to the approved 10 MiB ceiling |
| `TRANSCRIPTION_MAX_DURATION_SECONDS` | `60` | Positive, finite seconds up to the approved 60-second ceiling |

The timeout is enforced both through HTTPX and an overall async deadline.
Provider response bodies are bounded to 65536 bytes; transcript text must be a
nonblank string of at most 5000 characters. Nothing is silently truncated or
retried. `LLM_MODEL` remains independent and is not used for speech recognition.

Provider language mapping: `en` to `en`, `si` to `si`, `ta` to `ta`, and `si-Latn`
to spoken Sinhala `si`. `mixed` and `unknown` omit a forced provider language.
There is no guarantee of romanized Sinhala output, code-switch recognition, or
detected language. Hints are requested metadata, not verified detection results.

HTTP 200 synthetic response:

```json
{
  "transcript": "කෙනෙක් මගේ පස්සෙන් ආවා.",
  "requested_language_hint": "si",
  "review_required": true
}
```

Transcript text, including nonblank whitespace and Unicode, is returned unchanged.
There is no saved-report ID, stored draft, audio location, or identity field.
After reviewing/editing it, the user separately submits `POST /api/v1/reports`
with `original_text` equal to the final reviewed text, `input_method: "voice"`,
and the appropriate text-language hint. Omit `translated_text` to use existing
server-side English preparation. A Sinhala-script transcript normally uses `si`;
`si-Latn` describes final romanized text. This stateless endpoint cannot prove
that a human reviewed the draft. Existing translation-quality limitations remain.

| Status | Meaning / sanitized detail |
|---|---|
| `401`, `403` | Existing JWT credential/role rejection, before body consumption |
| `413` | `Audio exceeds the permitted size or duration.` |
| `415` | `Unsupported audio media type, container, or encoding.` |
| `422` | `Audio is invalid or contains no usable speech.`; includes upload timeout/disconnection |
| `422` | Invalid query hint uses the existing redacted validation detail list |
| `503` | `Audio transcription is unavailable.`; includes capacity, missing FFmpeg/FFprobe, process-launch errors, conversion timeout, missing credentials, provider HTTP/timeout errors, malformed JSON, missing/non-string text, and excessive output |
| `503` | Existing authentication configuration/JWKS outages retain `Authentication is unavailable.` |

All failures leave report storage untouched. Provider error bodies, tokens,
credentials, audio bytes, and transcript contents are not included in errors/logs.
WAV audio stays in memory. Compressed input uses a private temporary file for
seekable parsing; converted audio stays in bounded memory. Temporary input is
removed before provider invocation, including failure/timeout/cancellation paths.
Audio and drafts are never saved in Supabase. This is request-scoped handling,
not forensic secure erasure; see [privacy guidance](privacy-and-safety.md).

## Report creation: POST /api/v1/reports

The route accepts `ReportCreateRequest` and returns the unchanged `ReportResponse`. Send
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
| `translated_text` | No | Legacy English translation, nonblank and 1–5000 characters if supplied; omit for backend preparation; explicit null is invalid |
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
endpoint does not accept audio or perform transcription. Translation operates on
the final text and finishes before the database INSERT.

English (`en`) uses `original_text` unchanged as `translated_text`, without a
provider call, even if a different legacy translation was supplied. For other
hints, a supplied translation remains accepted for compatibility; it is not
independently checked for language, accuracy, or agreement with the original.
The response has no provider-verification or translation-quality guarantee.

When the translation is omitted, `si` and `ta` use Sinhala-to-English and
Tamil-to-English translation. `si-Latn`, `mixed`, and `unknown` use an explicitly
multilingual prompt; Latin script does not establish English. The backend uses
the caller's hint, not an implemented language detector. The adapter requires a
structured success result, rejects blank/oversized output, refusals, incomplete
generation, untranslated Sinhala/Tamil script, and obvious non-English echoes,
and fails when the model reports that it cannot translate reliably. These checks
do not establish semantic translation accuracy.

The original text is preserved exactly, including whitespace and Unicode.
`ReportSubmission` remains the strict prepared/persisted contract: its translation
is still mandatory and nonblank, as is `ReportResponse.translated_text`.

Synthetic request asking the backend to translate (no `translated_text`):

```json
{
  "original_text": "කෙනෙක් මගේ පස්සෙන් ආවා.",
  "language_hint": "si",
  "input_method": "text"
}
```

Translation uses the existing Groq HTTPX integration settings: `GROQ_API_KEY`,
`LLM_MODEL`, `LLM_BASE_URL`, and `LLM_TIMEOUT_SECONDS`. `LLM_API_KEY` is not an
automatic fallback. Missing provider credentials do not prevent English or
legacy translated submissions. No new provider dependency or migration is needed.

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
| `201` | English preparation succeeded and the Data API returned exactly one complete saved row with the expected owner and prepared values, validated through `ReportResponse` |
| `401` | Missing/malformed bearer credentials or invalid/unsupported token; includes `WWW-Authenticate: Bearer` |
| `403` | Verified token lacks the authenticated-user role |
| `422` | Request validation failed; `detail` is a list of `loc`, `msg`, and `type`, without submitted input values or validation context |
| `503` | Authentication, required translation, or storage is unavailable, or a provider response failed validation |

Authentication can pre-empt schema validation. Missing or invalid credentials return:

```json
{"detail": "Invalid or missing authentication credentials."}
```

Verification infrastructure failures use `{"detail": "Authentication is unavailable."}`.
Required translation failures use
`{"detail": "Report translation is unavailable. Report was not created."}`.
No INSERT is attempted in that case; untranslated text is never substituted as a
successful provider translation. There is no partial report, automatic translation
retry, or pending-translation record. The caller can retry with the original text.
Storage failures use `{"detail": "Report storage is unavailable."}`. Provider
errors, credentials, SQL details, and stack traces are not returned. A timeout or
invalid response can occur after a database commit; the backend does not claim
the report was unsaved and does not automatically retry creation. Idempotency is
not implemented.

## Analyze a stored report: POST /api/v1/incident-ai/analyze

Requires the same verified Supabase bearer identity as report creation. The
backend loads `id`, `user_id`, and `translated_text` from `reports`, filtering by
both `report_id` and the verified user's ID. It checks the returned IDs again
before classification. No public report retrieval endpoint is added.

```json
{
  "report_id": "4bf9b65c-9451-40e0-a40c-e26376a3a5d9"
}
```

`reported_time` and `street_lights` retain their existing optional analysis
contracts. Legacy `translated_text` is optional/deprecated and ignored; the
classifier always receives the owned report's stored English text. Caller-supplied
owner fields are rejected. Reporting does not automatically trigger classification.

The existing `IncidentAnalysis` response and HTTP 200 success contract are unchanged.
Missing/invalid authentication uses 401, insufficient role 403, and unavailable
verification/storage 503. A missing or foreign-owned report returns the same
`404` with `{"detail": "Report not found."}`, without calling classification.
Unreadable or invalid report rows return `503` with
`{"detail": "Report storage is unavailable."}`. Classification-provider failure
retains the existing `needs_review` fallback; analysis persistence failure retains
`503` with `{"detail": "Could not save incident analysis"}`. Neither changes the
already-created report.

## Adding endpoints

Agree on each contract before feature implementation. Record the HTTP method,
path, purpose, authentication requirements, Pydantic request and response schemas,
required and optional fields, validation rules, status codes, and error examples.
Specify pagination, timestamps/time zones, and coordinate conventions where relevant.
Use synthetic examples without personal data. Keep OpenAPI and contract tests
consistent, and coordinate breaking changes with the mobile team.

The safety map endpoints are documented below. Incident-analysis classification
and severity logic remain separate from report translation.

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

How the module works, and why each threshold is what it is, is documented in
[safety-map.md](safety-map.md). This section covers the contract only.

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
