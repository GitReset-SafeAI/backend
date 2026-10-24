BEGIN;

-- Requested removal: historical crowd-level values are discarded.
ALTER TABLE public.reports
    DROP COLUMN crowd_level,
    ADD COLUMN reported_time text,
    ADD CONSTRAINT reports_reported_time_check CHECK (
        reported_time IS NULL OR char_length(btrim(reported_time)) BETWEEN 1 AND 100
    );

COMMENT ON COLUMN public.reports.reported_time IS
    'User-supplied incident clock time or period. Analysis stores its normalized time-period category.';

NOTIFY pgrst, 'reload schema';
COMMIT;
