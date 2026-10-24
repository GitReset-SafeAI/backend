BEGIN;

-- Preserve historical free-text times before replacing them with enum values.
UPDATE public.incident_ai_analyses
SET extracted_details = extracted_details || jsonb_build_object(
    'original_reported_time', reported_time
)
WHERE reported_time IS NOT NULL AND reported_time NOT IN (
    'morning', 'afternoon', 'evening', 'night', 'late_night', 'not_sure'
);

-- Historical free text requires review; do not guess its period.
UPDATE public.incident_ai_analyses SET reported_time = 'not_sure'
WHERE reported_time IS NULL OR reported_time NOT IN (
    'morning', 'afternoon', 'evening', 'night', 'late_night', 'not_sure'
);

ALTER TABLE public.incident_ai_analyses
    ALTER COLUMN reported_time SET DEFAULT 'not_sure',
    ALTER COLUMN reported_time SET NOT NULL,
    ADD CONSTRAINT incident_ai_analyses_reported_time_check CHECK (reported_time IN (
        'morning', 'afternoon', 'evening', 'night', 'late_night', 'not_sure'
    ));

COMMIT;
