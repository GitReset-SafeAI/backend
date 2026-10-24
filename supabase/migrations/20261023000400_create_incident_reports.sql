-- Incident reports: the submitted report and where it happened.
--
-- The reporting module owns this table. It is proposed here because no branch
-- stored coordinates and hotspot work needs them; the columns follow the
-- project overview's reporting section rather than only what the map consumes,
-- so reporting can adopt and extend it instead of replacing it.
--
-- Shape notes for whoever picks this up:
--   * incident_ai_analyses.report_id points at incident_reports.id. No foreign
--     key is declared yet so the two modules can be developed and seeded
--     independently; add one once both write through the same path.
--   * Everything except the report body is optional. The overview is explicit
--     that a report must not be rejected for missing optional answers.
--   * Context columns reuse the incident_ai_analyses vocabularies, so a value
--     the reporter supplied and a value the AI inferred are directly
--     comparable and need no translation.

BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS public.incident_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),

    -- Identity is kept in its own nullable column so a report can be read,
    -- analysed and aggregated without touching it. Anonymous reports are a
    -- supported case, not a failure.
    reporter_id uuid,

    -- The report itself. Restrict this column to the reporter and moderation;
    -- it must never reach a public map response.
    description text NOT NULL CHECK (length(btrim(description)) > 0),
    input_method text NOT NULL DEFAULT 'text'
        CHECK (input_method IN ('text', 'voice')),
    -- The language written in, which is not the interface language.
    report_language text NOT NULL DEFAULT 'unknown'
        CHECK (report_language IN ('si', 'en', 'mixed', 'unknown')),

    -- When it happened. The exact timestamp is optional; the period is what
    -- the map filters on, and matches incident_ai_analyses.reported_time.
    occurred_at timestamptz,
    reported_time text NOT NULL DEFAULT 'not_sure'
        CHECK (reported_time IN (
            'morning', 'afternoon', 'evening', 'night', 'late_night', 'not_sure'
        )),

    -- Where it happened, when the reporter provided it and permitted its use.
    -- Null is ordinary: such a report is still analysed, it just cannot
    -- contribute to a hotspot.
    location geography(Point, 4326),
    location_source text
        CHECK (location_source IN ('device', 'map_pin', 'place_name')),
    location_accuracy_m numeric CHECK (location_accuracy_m >= 0),

    -- Optional context the reporter may give, in the AI's vocabularies.
    reported_street_lights text
        CHECK (reported_street_lights IN (
            'well_lit', 'partially_lit', 'poorly_lit_or_dark', 'day_light', 'not_sure'
        )),
    crowd_level text
        CHECK (crowd_level IN ('empty', 'few_people', 'busy', 'crowded', 'not_sure')),
    others_nearby boolean,

    -- Set by the integrity module. Defaults keep reports usable before that
    -- module exists, and record the assumption rather than hiding it.
    integrity_status text NOT NULL DEFAULT 'accepted'
        CHECK (integrity_status IN ('accepted', 'flagged', 'rejected')),
    is_duplicate boolean NOT NULL DEFAULT false,

    -- Separates demo rows from anything a person actually submitted.
    is_synthetic boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS incident_reports_location_idx
    ON public.incident_reports USING GIST (location);
CREATE INDEX IF NOT EXISTS incident_reports_reporter_idx
    ON public.incident_reports (reporter_id, created_at DESC);

-- The only thing the safety map reads. Eligibility is enforced here so the
-- clustering code cannot widen it by accident:
--   * a location is required, since an area needs coordinates
--   * only completed analyses; a report the AI could not read is not evidence
--   * nothing duplicated or rejected by the integrity module
-- The reporter, the description and the exact timestamp are deliberately not
-- selected: they are not needed to aggregate and must not leak through the map.
CREATE OR REPLACE VIEW public.map_eligible_incidents AS
SELECT
    r.id,
    r.occurred_at,
    ST_Y(r.location::geometry) AS latitude,
    ST_X(r.location::geometry) AS longitude,
    a."IncidentCategory"      AS incident_category,
    a."SafetyConcernCategory" AS safety_concern_category,
    -- What the reporter said about lighting is better evidence than what the
    -- AI inferred, so it wins where both exist.
    COALESCE(
        NULLIF(r.reported_street_lights, 'not_sure'),
        a.street_lights
    ) AS street_lights,
    COALESCE(
        NULLIF(r.reported_time, 'not_sure'),
        a.reported_time
    ) AS reported_time,
    a.severity,
    a.confidence
FROM public.incident_reports AS r
JOIN public.incident_ai_analyses AS a ON a.report_id = r.id
WHERE r.location IS NOT NULL
  AND r.integrity_status = 'accepted'
  AND r.is_duplicate = false
  AND a.analysis_status = 'completed';

COMMIT;
