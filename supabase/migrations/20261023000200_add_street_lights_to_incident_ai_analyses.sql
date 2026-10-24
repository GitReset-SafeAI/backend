BEGIN;

ALTER TABLE public.incident_ai_analyses
    ADD COLUMN street_lights text NOT NULL DEFAULT 'not_sure'
    CHECK (street_lights IN (
        'well_lit', 'partially_lit', 'poorly_lit_or_dark', 'day_light', 'not_sure'
    ));

COMMIT;
