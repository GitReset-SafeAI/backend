-- Extend reporting storage without rewriting the initial reports migration.
begin;

alter table public.reports
    add column lighting_condition text,
    add column crowd_level text,
    add column current_safety_status text,
    add constraint reports_lighting_condition_check check (
        lighting_condition in (
            'WELL_LIT', 'PARTIALLY_LIT', 'POORLY_LIT_OR_DARK', 'DAYLIGHT',
            'UNKNOWN_OR_PREFER_NOT_TO_SAY'
        )
    ),
    add constraint reports_crowd_level_check check (
        crowd_level in (
            'ALMOST_EMPTY', 'NOT_VERY_CROWDED', 'MODERATELY_CROWDED', 'VERY_CROWDED',
            'UNKNOWN_OR_PREFER_NOT_TO_SAY'
        )
    ),
    add constraint reports_current_safety_status_check check (
        current_safety_status in (
            'SAFE_NOW', 'UNSURE', 'MAY_STILL_BE_IN_DANGER', 'PREFER_NOT_TO_SAY'
        )
    );

-- The privileged creation path needs INSERT plus SELECT for return=representation.
-- Ordinary client grants and the existing no-policy RLS configuration stay intact.
revoke all privileges on table public.reports from service_role;
grant select, insert on table public.reports to service_role;

commit;
