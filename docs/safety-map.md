# Module 4 — Safety map, hotspots and route selection

How the safety map works end to end: where its data comes from, how reported
incidents become map areas, and — in most detail — how a route is chosen.

Written for whoever picks this module up, including its author six weeks from
now. It records the decisions and their reasons, not just the code.

---

## 1. What this module does

Turns eligible incident reports into two things:

1. **Reported-incident areas** on a map — aggregated, never individual reports.
2. **A comparison of routes** between two places, explaining what was reported
   along each one.

What it deliberately does **not** do: claim anywhere is safe, publish a
reporter's words or position, or present a recommendation as a guarantee.

## 2. Where the data comes from

Three modules feed this one:

| Source | Owner | What it provides |
|---|---|---|
| `public.reports` | reporting | `latitude`, `longitude`, `occurred_at` |
| `public.incident_ai_analyses` | incident-AI | category, severity, confidence, `street_lights`, `reported_time` |
| `integrity_status` *(not built yet)* | integrity | duplicate and anomaly flags |

They are joined by **one view**, `map_eligible_incidents`
(`supabase/migrations/20261023150000_…`). Eligibility lives in SQL rather than
Python so the clustering code cannot widen it by accident:

```sql
where r.latitude is not null
  and r.longitude is not null
  and a.analysis_status = 'completed'
```

The view also decides what is **not** published. It never selects `user_id`,
`original_text`, `translated_text`, or the reporter's own
`lighting_condition` / `crowd_level` / `current_safety_status` — the reporting
module records those as private context that is not approved for map responses.

**Agreed eligibility rules**

- `analysis_status = 'needs_review'` reports are **excluded entirely**. A report
  the AI could not read is not evidence of anything; it waits for a human.
- `confidence` **weights** a report rather than excluding it
  (`CONFIDENCE_FLOOR = 0.0`). A genuine report written in mixed Sinhala and
  English can score low without being false, and cutting those would drop
  exactly the reports this app exists to capture.

**Without a database**, the service reads `sample-data/synthetic_map_reports.json`
instead, so the whole module is demonstrable offline. Same shape, same
eligibility, nothing else differs. Regenerate it — and `supabase/seed.sql` —
with `python sample-data/generate_synthetic_reports.py`.

## 3. Hotspot detection

`app/modules/safety_map/hotspots.py`

### Clustering

**DBSCAN with the haversine metric**, so `eps` is a real distance on the ground
rather than a degree approximation that stretches with latitude:

```python
eps = CLUSTER_EPS_M / EARTH_RADIUS_M     # 150 m, expressed in radians
min_samples = MIN_REPORTS_PER_HOTSPOT    # 3
metric = "haversine"                     # coordinates passed in radians
```

`min_samples` is deliberately the same number as the publication floor, so an
area too small to publish is never formed in the first place.

### Privacy

Three rules, enforced in the Pydantic model as well as the code, so a violation
fails validation rather than reaching the app:

| Rule | Value | Why |
|---|---|---|
| Minimum reports | 3 | k-anonymity: fewer could identify one reporter |
| Minimum radius | 150 m | A tight radius around 3 points locates them |
| Centre rounding | 3 decimals (~110 m) | The published centre cannot be traced back |

Clusters dropped for being too small are counted in `suppressed_area_count`,
so the map can say areas were withheld rather than showing blank space that
reads as safety.

### Intensity

Each report contributes a weight; the cluster's total is normalised against the
busiest cluster in the view:

```
weight = (severity / 10) × confidence × 0.5 ^ (age_days / 30)
```

- **severity** is her `smallint 0–10` column, not a three-level label.
- **confidence** weights rather than filters, as above.
- **recency** halves every 30 days, so a month-old report counts for half.

### Lighting

Taken from `incident_ai_analyses.street_lights`. An area reports
`POOR_STREET_LIGHTING` only when **at least half** its reports say
`poorly_lit_or_dark` (`POOR_LIGHTING_SHARE = 0.5`). `not_sure` and `day_light`
say nothing about a place and are ignored rather than counted as "fine".

### Evidence level

Every response carries `evidence_level`:

| Value | Meaning |
|---|---|
| `insufficient` | Not enough reports to show a pattern |
| `limited` | Based on a small number of reports |
| `moderate` | ≥6 reports and mean confidence ≥0.6 |

An area with no hotspots returns `insufficient` — **never** an implication of
safety. This is the single most important honesty rule in the module.

---

## 4. Route selection

`app/modules/safety_map/routing.py` and `service.py`. This is the part worth
understanding properly.

### 4.1 Where the geometry comes from

Walking and driving routes come from **OSRM** over OpenStreetMap, via the public
demo server.

> **Important limitation.** That server serves **only the car profile**. The
> `foot`, `bike` and `driving` endpoints all return identical geometry and
> timings — verified directly. So a "walking" route follows the road network,
> and the app says so rather than implying a pedestrian route.
> `pedestrian_profile_available` is `false` in every response.

Both OpenStreetMap services are called with an identifying `User-Agent`, as
their usage policies require. Photon returns **403** without one.

### 4.2 Generating candidates

1. **Ask OSRM for alternatives** (`alternatives=3`).
2. **If it returns fewer than three**, which is common on short journeys,
   request further routes through a **waypoint offset to one side** of the
   direct line. These are still real street routes, not drawn lines. A
   candidate is kept only if its length differs by more than 5% from one
   already held, so the same road is not listed twice.
3. **If OSRM cannot be reached at all**, fall back to bent lines between the
   two points, flagged `uses_street_network: false` so the app can say the
   paths are approximate rather than passing them off as directions.

### 4.3 Scoring exposure

For each candidate:

1. **Resample** the geometry to a point every `SAMPLE_SPACING_M = 25 m`, so a
   long straight leg is not under-counted relative to a winding one.
2. For each sample, sum a **Gaussian kernel** over every area:

```
risk(point) = Σ  intensity × mean_confidence × exp( −d² / (2 × 120²) )
```

`EXPOSURE_SIGMA_M = 120 m`, so an area's influence decays smoothly instead of
stopping abruptly at its edge.

3. `exposure_score` = mean risk across samples, scaled to 0–100.

It is a **relative indicator of proximity to reported areas**, not a safety
score, and never a letter grade. `hotspots_passed` — the number of areas a
route actually enters — is the number worth saying out loud.

### 4.4 The three answers

A single "best route" would be dishonest, because three different questions
have three different answers. Every response carries all three.

#### `recommended_route_id` — a real improvement worth a modest detour

The strictest of the three. **All** of these must hold:

| Condition | Constant | Why |
|---|---|---|
| The fastest route actually passes an area | `hotspots_passed > 0` | Nothing to avoid means no case for walking further |
| …and its exposure is not negligible | `NEGLIGIBLE_EXPOSURE = 5.0` | Below this the difference is noise |
| Exposure drops by ≥25% | `MIN_EXPOSURE_REDUCTION = 0.25` | Relative improvement |
| **and** by ≥5 points absolute | `MIN_ABSOLUTE_EXPOSURE_DROP = 5.0` | 0.4 → 0.3 is a 25% drop and means nothing |
| Detour ≤ max(5 min, 30%) | `MAX_EXTRA_SECONDS`, `MAX_EXTRA_FRACTION` | A recommendation must be practical |

`null` is a valid, common answer. Earlier versions lacked the absolute floor
and recommended seven extra minutes of walking to avoid nothing — the relative
test alone is not enough.

#### `lowest_exposure_route_id` — the least exposed of those compared

Always set. "Nothing qualified" is a poor answer on its own, so the best
available option is named even when it does not clear the recommendation bar.
Ties go to the quicker route.

#### `clear_route_id` — enters no reported area at all

**Deliberately not subject to the detour allowance.** How much longer is worth
it is the traveller's decision, not the service's. If every candidate runs
through an area, the street network is asked for one that goes round:

```
for clearance in (1.5, 3.0, 5.0):        # multiples of the area's radius
    for side in (+1, −1):                # left and right of the road
        waypoint = area centre + perpendicular × (radius × clearance + 200 m)
        ask OSRM to route through it
        if the result enters no area: done
```

Two details that make this work, both of which were bugs first:

- **The offset is perpendicular to the path's own heading**, not along the line
  from the area's centre. Where a road runs through an area — Galle Road does —
  offsetting the latter way pushes the waypoint further down the *same road*
  and OSRM snaps it straight back. Closest approach stayed stuck at 68 m
  regardless of clearance until this was fixed.
- **Both sides are tried**, because one of them may be the sea.

#### Unavoidable areas

An area containing the **start** or the **destination** cannot be routed away
from. Those are excluded from what a route is expected to avoid, and reported
as `origin_in_reported_area` / `destination_in_reported_area` so the app can
explain why rather than leaving every route looking unavoidably exposed.

Without this, a journey ending inside an area could never produce a clear
route, and the reader would reasonably think the routing had failed.

### 4.5 Travel modes

| Mode | Duration from | Why |
|---|---|---|
| `driving` | OSRM's own duration | The service's profile is a car, so its timing is accurate |
| `walking` | `distance / 1.35 m/s` | There is no pedestrian profile to time against |

The geometry is identical in both cases. Only the timing differs, and the app
says so.

### 4.6 A clear route may not exist

Sometimes the corridor is simply covered. On one tested journey the best
detour reached 228 m from an area with a 277 m radius — close, but still
inside. The honest answer is then the least-exposed route and no clear one,
rather than a fabricated alternative.

---

## 5. Place search

`GET /api/v1/safety-map/places?q=…` proxies **Photon** over OpenStreetMap, so
place names and routes come from the same data. Proxying keeps the provider —
and any future key — on the server, and lets one cache serve every user. Results
are biased to a Sri Lanka bounding box so a two-letter query does not return
results from across the world.

A suggestion's id includes its **position**, because Photon can return the same
OSM id more than once for a long road; the id alone is not unique and React
warns about duplicate keys, which can drop or duplicate rows.

## 6. API surface

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/safety-map/hotspots` | Aggregated areas in a bounding box |
| `POST /api/v1/safety-map/routes/compare` | Route options between two points |
| `GET /api/v1/safety-map/places` | Place search for the journey planner |

Conventions, which differ from the services underneath and are converted once,
at the boundary:

- Coordinates are always `{"latitude": …, "longitude": …}` objects. GeoJSON
  `[longitude, latitude]` and OSRM's `lon,lat` ordering never cross the API.
- The `bbox` query parameter is `min_lon,min_lat,max_lon,max_lat` and is capped
  at 2° per side, so one call cannot scan the country.
- Timestamps are UTC ISO-8601. No report carries its exact time outward; areas
  report a **day count** and a period.

## 7. The mobile side

`GitReset-Frontend/src/features/safety/`

| File | Responsibility |
|---|---|
| `screens/SafetyMapScreen.tsx` | State and composition |
| `components/JourneyPlanner.tsx` | Start/destination, travel mode, compare |
| `components/PlaceSearchField.tsx` | One debounced search box (250 ms) |
| `components/SafetyMapCanvas.tsx` | Map, circles, markers, route lines |
| `components/RouteComparisonCard.tsx` | The route list and its explanations |
| `components/HotspotDetailCard.tsx` | One area's detail |
| `components/TimePeriodSelector.tsx` | Time-of-day filter |
| `constants/issueTypes.ts` | Category code → icon and label |

Interface decisions worth keeping:

- **The time filter defaults to the period the device clock is in**, with
  boundaries matching the backend's `normalize_reported_time` exactly, so a
  report filed now falls in the period the map defaults to. Opening on "any
  time" averaged a night walk together with daytime reports.
- **Map taps do not set points.** Marker taps and point-setting shared one
  gesture, so opening an area silently rewrote the journey. Points come from
  search; the current-location button always sets the start.
- **Unrecognised category codes degrade** to a neutral label, so the incident-AI
  module can add a category without breaking the map.
- **Colour never carries meaning alone** — every area and route is also
  labelled, and a recommendation is stated in words.
- **Intensity drives opacity only.**

### Running it

```bash
# backend
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload

# mobile: .env
EXPO_PUBLIC_DATA_MODE=api                        # or 'mock' to work offline
EXPO_PUBLIC_API_BASE_URL=http://<your-LAN-ip>:8010
```

The phone cannot reach `127.0.0.1`; it needs the machine's LAN address, and the
server must bind `0.0.0.0`. Restart Metro after editing `.env`. In `api` mode
there is **no silent fallback to mock data** — a missing backend fails
explicitly, by design.

`<MapView>` is a native component, so a development build is needed
(`npx expo run:ios` / `run:android`); Android also needs
`GOOGLE_MAPS_ANDROID_API_KEY`. iOS uses Apple Maps and needs no key.

## 8. Known limits

1. **No pedestrian routing profile.** See §4.1.
2. **The SQL has never been executed.** The migrations and seed are written but
   unverified against a real PostGIS instance.
3. **Integrity flags are not applied.** Every eligible report counts once;
   duplicates are not yet filtered, because that module does not exist.
4. **Hotspots reflect reporting, not danger.** Busy areas attract more reports.
   Absence of reports is absence of evidence. The app says this; the data
   cannot say otherwise.

## 9. Tests

`tests/test_safety_map.py` (backend) and `src/test/safetyMap*.test.ts(x)`
(mobile). The ones that encode decisions rather than mechanics:

- A hotspot below the floor, or with too tight a radius, fails schema validation.
- An empty area returns `insufficient` evidence, never an implication of safety.
- A low-confidence area scores lower exposure than a confident one.
- No recommendation is made when the fastest route already avoids everything.
- A clear route is offered even when it takes much longer.
- Avoidance waypoints step sideways off a road, not along it.
- A map tap leaves a planned journey unchanged.
- API mode fails explicitly rather than serving synthetic data.
