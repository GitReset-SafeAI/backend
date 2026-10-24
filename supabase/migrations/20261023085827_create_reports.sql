-- Initial reporting storage. Supabase supplies auth.users and the client roles.
begin;

create table public.reports (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete restrict,
    original_text text not null,
    translated_text text not null,
    input_method text not null,
    language_hint text not null,
    latitude double precision,
    longitude double precision,
    occurred_at timestamptz,
    submitted_at timestamptz not null default now(),

    -- Validate trimmed content without changing the original stored text.
    constraint reports_original_text_length_check check (
        char_length(regexp_replace(original_text, '^[[:space:]]+|[[:space:]]+$', '', 'g'))
        between 1 and 5000
    ),
    constraint reports_translated_text_length_check check (
        char_length(regexp_replace(translated_text, '^[[:space:]]+|[[:space:]]+$', '', 'g'))
        between 1 and 5000
    ),
    constraint reports_input_method_check check (input_method in ('text', 'voice')),
    constraint reports_language_hint_check check (
        language_hint in ('si', 'si-Latn', 'en', 'ta', 'mixed', 'unknown')
    ),
    constraint reports_latitude_check check (latitude between -90 and 90),
    constraint reports_longitude_check check (longitude between -180 and 180),
    constraint reports_coordinates_pair_check check (
        (latitude is null) = (longitude is null)
    )
);

create index reports_user_id_idx on public.reports (user_id);

alter table public.reports enable row level security;

-- No client access until verified ownership and the backend access path are agreed.
-- Revoke default grants as well as enabling RLS; no permissive policies are added.
revoke all privileges on table public.reports from public, anon, authenticated;

commit;
