-- Manual PostgreSQL check, only in a new disposable local test database.
\set ON_ERROR_STOP on

do $$
begin
    if current_database() !~ '^saferoute_report_test_[0-9]+$'
        or inet_server_addr() is distinct from '127.0.0.1'::inet then
        raise exception 'Use a disposable loopback database named saferoute_report_test_<timestamp>';
    end if;
    if to_regclass('auth.users') is not null then
        raise exception 'Refusing to modify an existing Auth schema';
    end if;
    if not exists (select from pg_roles where rolname = 'anon') then
        create role anon nologin;
    end if;
    if not exists (select from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin;
    end if;
    if not exists (select from pg_roles where rolname = 'service_role') then
        create role service_role nologin bypassrls;
    end if;
end;
$$;

-- Test-only substitutes; the application migrations do not create Auth objects.
create schema auth;
create table auth.users (id uuid primary key);
insert into auth.users values ('11111111-1111-4111-8111-111111111111');
alter default privileges in schema public grant all on tables
    to public, anon, authenticated, service_role;

\ir ../../supabase/migrations/20261023085827_create_reports.sql
insert into public.reports (user_id, original_text, translated_text, input_method, language_hint)
values ('11111111-1111-4111-8111-111111111111', 'Synthetic existing report',
    'Synthetic existing report', 'text', 'en');
\ir ../../supabase/migrations/20261023140715_add_report_context.sql

begin;
set plpgsql.check_asserts = on;

create function pg_temp.new_report(overrides jsonb default '{}'::jsonb)
returns public.reports language plpgsql as $$
declare
    payload jsonb := '{"user_id":"11111111-1111-4111-8111-111111111111",
        "original_text":"Synthetic original", "translated_text":"Synthetic translation",
        "input_method":"text", "language_hint":"en"}'::jsonb || overrides;
    saved public.reports;
begin
    insert into public.reports (
        user_id, original_text, translated_text, input_method, language_hint,
        latitude, longitude, occurred_at, lighting_condition, crowd_level, current_safety_status
    ) values (
        (payload->>'user_id')::uuid, payload->>'original_text', payload->>'translated_text',
        payload->>'input_method', payload->>'language_hint',
        (payload->>'latitude')::double precision, (payload->>'longitude')::double precision,
        (payload->>'occurred_at')::timestamptz, payload->>'lighting_condition',
        payload->>'crowd_level', payload->>'current_safety_status'
    ) returning * into saved;
    return saved;
end;
$$;

create function pg_temp.expect_failure(statement text, expected_state text)
returns void language plpgsql as $$
declare
    actual_state text;
begin
    begin
        execute statement;
    exception when others then
        get stacked diagnostics actual_state = returned_sqlstate;
        if actual_state <> expected_state then
            raise exception 'Expected SQLSTATE %, received %', expected_state, actual_state;
        end if;
        return;
    end;
    raise exception 'Expected SQLSTATE %, but statement succeeded', expected_state;
end;
$$;

do $$
declare
    saved public.reports;
    sample record;
    context_field text;
begin
    assert (select count(*) = 13 from information_schema.columns
        where table_schema = 'public' and table_name = 'reports');
    assert (select count(*) = 3 from information_schema.columns
        where table_schema = 'public' and table_name = 'reports'
        and column_name in ('lighting_condition', 'crowd_level', 'current_safety_status')
        and data_type = 'text' and is_nullable = 'YES' and column_default is null);
    assert exists (select from public.reports where original_text = 'Synthetic existing report'
        and lighting_condition is null and crowd_level is null and current_safety_status is null);

    saved := pg_temp.new_report();
    assert saved.id is not null and saved.submitted_at = current_timestamp;
    assert saved.lighting_condition is null and saved.crowd_level is null
        and saved.current_safety_status is null;
    saved := pg_temp.new_report('{"lighting_condition":null,"crowd_level":null,
        "current_safety_status":null}'::jsonb);
    assert saved.lighting_condition is null and saved.crowd_level is null
        and saved.current_safety_status is null;

    saved := pg_temp.new_report('{"latitude":6.5,"longitude":80.5,
        "occurred_at":"2026-10-23T19:00:00+05:30","lighting_condition":"PARTIALLY_LIT",
        "crowd_level":"ALMOST_EMPTY","current_safety_status":"MAY_STILL_BE_IN_DANGER"}'::jsonb);
    assert saved.latitude = 6.5 and saved.longitude = 80.5;
    assert saved.occurred_at = '2026-10-23T13:30:00Z'::timestamptz;
    assert saved.lighting_condition = 'PARTIALLY_LIT' and saved.crowd_level = 'ALMOST_EMPTY'
        and saved.current_safety_status = 'MAY_STILL_BE_IN_DANGER';

    for sample in select * from (values
        ('lighting_condition', 'WELL_LIT'), ('lighting_condition', 'PARTIALLY_LIT'),
        ('lighting_condition', 'POORLY_LIT_OR_DARK'), ('lighting_condition', 'DAYLIGHT'),
        ('lighting_condition', 'UNKNOWN_OR_PREFER_NOT_TO_SAY'),
        ('crowd_level', 'ALMOST_EMPTY'), ('crowd_level', 'NOT_VERY_CROWDED'),
        ('crowd_level', 'MODERATELY_CROWDED'), ('crowd_level', 'VERY_CROWDED'),
        ('crowd_level', 'UNKNOWN_OR_PREFER_NOT_TO_SAY'),
        ('current_safety_status', 'SAFE_NOW'), ('current_safety_status', 'UNSURE'),
        ('current_safety_status', 'MAY_STILL_BE_IN_DANGER'),
        ('current_safety_status', 'PREFER_NOT_TO_SAY')
    ) as options(field_name, value) loop
        saved := pg_temp.new_report(jsonb_build_object(sample.field_name, sample.value));
        assert to_jsonb(saved)->>sample.field_name = sample.value;
    end loop;
    foreach context_field in array array['lighting_condition', 'crowd_level', 'current_safety_status'] loop
        perform pg_temp.expect_failure(format('select pg_temp.new_report(%L::jsonb)',
            jsonb_build_object(context_field, 'invalid')::text), '23514');
    end loop;
    perform pg_temp.expect_failure('select pg_temp.new_report(
        ''{"user_id":"22222222-2222-4222-8222-222222222222"}''::jsonb)', '23503');
    perform pg_temp.expect_failure('delete from auth.users
        where id = ''11111111-1111-4111-8111-111111111111''', '23503');
    assert (select confdeltype = 'r' from pg_constraint
        where conrelid = 'public.reports'::regclass and conname = 'reports_user_id_fkey');
    assert (select relrowsecurity from pg_class where oid = 'public.reports'::regclass);
    assert not exists (select from pg_policy where polrelid = 'public.reports'::regclass);
    assert not has_table_privilege('anon', 'public.reports', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE');
    assert not has_table_privilege('authenticated', 'public.reports', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE');
    assert has_table_privilege('service_role', 'public.reports', 'SELECT');
    assert has_table_privilege('service_role', 'public.reports', 'INSERT');
    assert not has_table_privilege('service_role', 'public.reports', 'UPDATE,DELETE,TRUNCATE');
    raise notice 'PASS: context fields, nulls, existing rows, defaults, enum checks, owner FK and grants';
end;
$$;

set local role service_role;
do $$
declare
    new_id uuid;
    new_time timestamptz;
begin
    insert into public.reports (user_id, original_text, translated_text, input_method, language_hint,
        lighting_condition, crowd_level, current_safety_status)
    values ('11111111-1111-4111-8111-111111111111', 'Synthetic service insert',
        'Synthetic service insert', 'text', 'en', 'DAYLIGHT', 'VERY_CROWDED', 'SAFE_NOW')
    returning id, submitted_at into new_id, new_time;
    assert new_id is not null and new_time = current_timestamp;
    assert exists (select from public.reports where id = new_id
        and lighting_condition = 'DAYLIGHT' and crowd_level = 'VERY_CROWDED'
        and current_safety_status = 'SAFE_NOW');
    begin
        update public.reports set original_text = 'Unexpected update';
        raise exception 'Unexpected service-role update permission';
    exception when insufficient_privilege then null;
    end;
    raise notice 'PASS: privileged INSERT RETURNING works and UPDATE is denied';
end;
$$;
reset role;
rollback;
