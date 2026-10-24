BEGIN;

ALTER TABLE public.reports
    ADD COLUMN "RAPID_SUBMISSIONS" boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS reports_user_submitted_at_idx
    ON public.reports (user_id, submitted_at DESC);

CREATE TABLE IF NOT EXISTS public.integrity_rapid_submission_settings (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    threshold integer NOT NULL DEFAULT 5 CHECK (threshold BETWEEN 2 AND 1000),
    window_minutes integer NOT NULL DEFAULT 5 CHECK (window_minutes BETWEEN 1 AND 1440)
);
INSERT INTO public.integrity_rapid_submission_settings (id) VALUES (true)
    ON CONFLICT (id) DO NOTHING;
ALTER TABLE public.integrity_rapid_submission_settings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.integrity_rapid_submission_settings FROM PUBLIC, anon, authenticated;
GRANT SELECT, UPDATE ON public.integrity_rapid_submission_settings TO service_role;

-- Replace the earlier separate-flag trigger if it was installed.
DROP TRIGGER IF EXISTS reports_flag_rapid_submissions ON public.reports;

CREATE OR REPLACE FUNCTION public.flag_rapid_report_submission()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    configured_threshold integer;
    configured_window integer;
    existing_count integer;
    checked_at timestamptz;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(NEW.user_id::text, 0)
    );
    checked_at := pg_catalog.clock_timestamp();
    SELECT threshold, window_minutes INTO STRICT configured_threshold, configured_window
    FROM public.integrity_rapid_submission_settings WHERE id = true;
    SELECT count(*) INTO existing_count FROM public.reports
    WHERE user_id = NEW.user_id
      AND submitted_at >= checked_at - pg_catalog.make_interval(mins => configured_window)
      AND submitted_at <= checked_at;

    -- The incoming report counts toward the threshold. Never trust a client flag.
    NEW."RAPID_SUBMISSIONS" := existing_count + 1 >= configured_threshold;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.flag_rapid_report_submission()
    FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER reports_flag_rapid_submissions
BEFORE INSERT ON public.reports
FOR EACH ROW EXECUTE FUNCTION public.flag_rapid_report_submission();

COMMENT ON COLUMN public.reports."RAPID_SUBMISSIONS" IS
    'Submission count reached the configured rolling-window threshold; review signal, not proof of spam.';

COMMIT;
