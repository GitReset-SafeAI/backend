-- Uses the guarded disposable local database fixture; never run against production.
\set ON_ERROR_STOP on
\ir test_report_creation.sql
\ir ../../supabase/migrations/20261024000200_add_rapid_submissions_flag_to_reports.sql
BEGIN;
SET plpgsql.check_asserts = on;
-- Repeat the fixture guard here so copying the transaction cannot bypass it.
DO $$
BEGIN
    IF current_database() !~ '^saferoute_report_test_[0-9]+$'
        OR inet_server_addr() IS DISTINCT FROM '127.0.0.1'::inet THEN
        RAISE EXCEPTION 'Disposable loopback test database required; do not run in Supabase';
    END IF;
END;
$$;
DELETE FROM public.reports;
INSERT INTO auth.users (id) VALUES ('22222222-2222-4222-8222-222222222222');
DO $$
DECLARE
    owner_id uuid := '11111111-1111-4111-8111-111111111111';
    saved public.reports;
    n integer;
BEGIN
    INSERT INTO public.reports (
        user_id, original_text, translated_text, input_method, language_hint, submitted_at
    ) VALUES (owner_id, 'Old synthetic', 'Old synthetic', 'text', 'en', now() - interval '6 minutes');
    FOR n IN 1..5 LOOP
        INSERT INTO public.reports (
            user_id, original_text, translated_text, input_method, language_hint
        ) VALUES ('22222222-2222-4222-8222-222222222222', 'Other user', 'Other user', 'text', 'en');
    END LOOP;
    FOR n IN 1..6 LOOP
        INSERT INTO public.reports (
            user_id, original_text, translated_text, input_method, language_hint, "RAPID_SUBMISSIONS"
        ) VALUES (owner_id, 'Synthetic', 'Synthetic', 'text', 'en', n < 5)
        RETURNING * INTO saved;
        ASSERT saved."RAPID_SUBMISSIONS" = (n >= 5);
    END LOOP;
    UPDATE public.integrity_rapid_submission_settings SET threshold = 10 WHERE id = true;
    INSERT INTO public.reports (
        user_id, original_text, translated_text, input_method, language_hint
    ) VALUES (owner_id, 'Synthetic', 'Synthetic', 'text', 'en') RETURNING * INTO saved;
    ASSERT saved."RAPID_SUBMISSIONS" = false;
    RAISE NOTICE 'PASS: threshold, other accounts, expired window, client override and configuration';
END;
$$;
ROLLBACK;
