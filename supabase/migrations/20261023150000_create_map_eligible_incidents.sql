-- The only thing the safety map reads.
--
-- Depends on public.reports (reporting module) and public.incident_ai_analyses
-- (incident-AI module), so it must be applied after both of theirs.
--
-- Eligibility is enforced here rather than in Python, so the clustering code
-- cannot widen it by accident:
--   * coordinates are required; an area cannot be placed without them
--   * only completed analyses, because a report the AI could not read is not
--     evidence of anything and waits for review
--
-- Deliberately NOT selected, and these omissions are the point of the view:
--   * user_id, original_text and translated_text, which identify a reporter or
--     carry their words
--   * submitted_at and occurred_at to the second; only the period and a day
--     count leave this module
--   * reports.lighting_condition, reports.crowd_level and
--     reports.current_safety_status, which the reporting module records as
--     private self-reported context that is not approved for map responses.
--     Lighting on the map comes from incident_ai_analyses.street_lights, which
--     the incident-AI module publishes, and only in aggregate across several
--     reports. Whether the reporter's own lighting answer may also be
--     aggregated is an open question for the team, not an assumption to make
--     here.

begin;

create or replace view public.map_eligible_incidents
with (security_invoker = true) as
select
    r.id,
    r.occurred_at,
    r.latitude,
    r.longitude,
    a."IncidentCategory"      as incident_category,
    a."SafetyConcernCategory" as safety_concern_category,
    a.street_lights,
    a.reported_time,
    a.severity,
    a.confidence
from public.reports as r
join public.incident_ai_analyses as a on a.report_id = r.id
where r.latitude is not null
  and r.longitude is not null
  and a.analysis_status = 'completed';

-- The view runs as the caller (security_invoker), so it grants no access the
-- caller does not already have. reports stays revoked from public, anon and
-- authenticated; only the backend's service role may read the aggregate source.
revoke all privileges on public.map_eligible_incidents from public, anon, authenticated;
grant select on public.map_eligible_incidents to service_role;

commit;
