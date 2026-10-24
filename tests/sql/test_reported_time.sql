-- Uses the guarded disposable local fixture, never the Supabase SQL editor.
\set ON_ERROR_STOP on
\ir test_report_creation.sql
\ir ../../supabase/migrations/20261024000300_replace_report_crowd_level_with_reported_time.sql
BEGIN;
SET plpgsql.check_asserts = on;
DO $$
DECLARE
    saved public.reports;
BEGIN
    ASSERT NOT EXISTS (
        SELECT FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'reports' AND column_name = 'crowd_level'
    );
    INSERT INTO public.reports (
        user_id, original_text, translated_text, input_method, language_hint,
        reported_time, lighting_condition
    ) VALUES (
        '11111111-1111-4111-8111-111111111111', 'Synthetic', 'Synthetic', 'text', 'en',
        '02:30', 'POORLY_LIT_OR_DARK'
    ) RETURNING * INTO saved;
    ASSERT saved.reported_time = '02:30';
    BEGIN
        UPDATE public.reports SET reported_time = ' ' WHERE id = saved.id;
        RAISE EXCEPTION 'Expected invalid time constraint violation';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    RAISE NOTICE 'PASS: reported_time replaces crowd_level and stores supplied time';
END;
$$;
ROLLBACK;
