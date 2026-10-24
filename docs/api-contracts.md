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

## Adding endpoints

Agree on each contract before feature implementation. Record the HTTP method,
path, purpose, authentication requirements, Pydantic request and response schemas,
required and optional fields, validation rules, status codes, and error examples.
Specify pagination, timestamps/time zones, and coordinate conventions where relevant.
Use synthetic examples without personal data. Keep OpenAPI and contract tests
consistent, and coordinate breaking changes with the mobile team.

Reporting, classification, severity, anomaly detection, hotspots, and route
recommendations have no endpoints or contracts implemented yet.

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
      "top_categories": ["verbal_harassment", "stalking"],
      "peak_time_bucket": "evening",
      "most_recent_report_days_ago": 2,
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
`issue_categories`, and `extra_duration_s` relative to the fastest route, so the
app can explain each path rather than only rank them. `exposure_score` is a
relative indicator of proximity to reported-incident areas and must never be
presented as a safety guarantee.

`recommended_route_id` is `null` when no candidate meaningfully lowers exposure
within an acceptable detour. The agreed rule, applied server-side and mirrored
in the mobile mock: recommend only when exposure drops by at least 25% and the
detour adds no more than `max(300 s, 30% of the fastest route)`.

### Open items

Category vocabulary and severity labels come from the incident-AI module;
hotspot eligibility (`integrity_status`, duplicate flags) comes from the
integrity module. Until both exist, Module 4 reads a `map_eligible_incidents`
view over synthetic seed data and weights every report equally.
