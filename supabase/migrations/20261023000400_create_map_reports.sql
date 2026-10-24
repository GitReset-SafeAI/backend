-- Placeholder location store for the safety map.
--
-- The reporting module owns incident reports and will replace this table with
-- its own. It is created here so hotspot work is not blocked: no other branch
-- stores coordinates, and incident_ai_analyses is keyed by report_id only.
--
-- Coordinates are stored as PostGIS geography so distance queries are in
-- metres. The map never publishes a row from this table directly.

BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS public.incident_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    occurred_at timestamptz,
    location geography(Point, 4326) NOT NULL,
    -- Set by the integrity module once it exists; until then every report is
    -- accepted, which is recorded here rather than assumed silently.
    integrity_status text NOT NULL DEFAULT 'accepted'
        CHECK (integrity_status IN ('accepted', 'flagged', 'rejected')),
    is_duplicate boolean NOT NULL DEFAULT false,
    -- Clearly separates demo rows from anything real.
    is_synthetic boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS incident_reports_location_idx
    ON public.incident_reports USING GIST (location);

-- The only thing the safety map reads. Eligibility is enforced here so the
-- clustering code cannot accidentally include ineligible reports:
--   * only completed analyses (a report the AI could not read is not evidence)
--   * no duplicates, nothing rejected by the integrity module
CREATE OR REPLACE VIEW public.map_eligible_incidents AS
SELECT
    r.id,
    r.occurred_at,
    ST_Y(r.location::geometry) AS latitude,
    ST_X(r.location::geometry) AS longitude,
    a."IncidentCategory"       AS incident_category,
    a."SafetyConcernCategory"  AS safety_concern_category,
    a.street_lights,
    a.reported_time,
    a.severity,
    a.confidence
FROM public.incident_reports AS r
JOIN public.incident_ai_analyses AS a ON a.report_id = r.id
WHERE r.integrity_status = 'accepted'
  AND r.is_duplicate = false
  AND a.analysis_status = 'completed';

COMMIT;
