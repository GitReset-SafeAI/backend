begin;

create table public.emergency_contacts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    name text not null check (char_length(btrim(name)) between 1 and 60),
    phone text not null check (phone ~ '^\+94[1-9][0-9]{8}$'),
    relationship text check (relationship in ('family', 'friend', 'partner', 'other')),
    slot smallint not null check (slot between 0 and 4),
    primary_at timestamptz not null default 'epoch',
    created_at timestamptz not null default now(),
    unique (user_id, slot)
);

alter table public.emergency_contacts enable row level security;
revoke all on public.emergency_contacts from public, anon;
grant select, insert, update, delete on public.emergency_contacts to authenticated;
create policy emergency_contacts_owner on public.emergency_contacts
    for all to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

-- Five unique slots enforce the limit even for direct Data API requests.
-- Serialize slot allocation per owner so simultaneous adds do not collide.
create function public.add_emergency_contact(
    p_name text, p_phone text, p_relationship text default null
) returns setof public.emergency_contacts
language plpgsql security invoker set search_path = '' as $$
declare
    owner_id uuid := auth.uid();
    available_slot smallint;
begin
    if owner_id is null then
        raise insufficient_privilege using message = 'Authentication required';
    end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(owner_id::text, 0));
    select s::smallint into available_slot
    from pg_catalog.generate_series(0, 4) as s
    where not exists (
        select 1 from public.emergency_contacts as c
        where c.user_id = owner_id and c.slot = s
    ) order by s limit 1;
    if available_slot is null then
        raise sqlstate 'PT409' using message = 'Emergency contact limit reached';
    end if;
    return query insert into public.emergency_contacts (user_id, name, phone, relationship, slot)
        values (owner_id, btrim(p_name), p_phone, p_relationship, available_slot)
        returning *;
end;
$$;

revoke all on function public.add_emergency_contact(text, text, text) from public, anon;
grant execute on function public.add_emergency_contact(text, text, text) to authenticated;

commit;
