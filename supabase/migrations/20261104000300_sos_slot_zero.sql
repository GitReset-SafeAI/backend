begin;

-- Allow an atomic slot swap without an intermediate unique-key violation.
alter table public.emergency_contacts
    drop constraint emergency_contacts_user_id_slot_key;
alter table public.emergency_contacts
    add constraint emergency_contacts_user_id_slot_key
    unique (user_id, slot) deferrable initially immediate;

-- Repair gaps left by deleting slot 0 before this migration. Existing slot-0
-- contacts remain unchanged; timestamps no longer determine the SOS target.
with candidates as (
    select distinct on (c.user_id) c.id
    from public.emergency_contacts c
    where not exists (
        select 1 from public.emergency_contacts z
        where z.user_id = c.user_id and z.slot = 0
    )
    order by c.user_id, c.slot
)
update public.emergency_contacts set slot = 0
where id in (select id from candidates);

create function public.make_primary_emergency_contact(p_contact_id uuid)
returns setof public.emergency_contacts
language plpgsql security invoker set search_path = '' as $$
declare
    owner_id uuid := auth.uid();
    chosen_slot smallint;
begin
    if owner_id is null then
        raise insufficient_privilege using message = 'Authentication required';
    end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(owner_id::text, 0));
    select slot into chosen_slot from public.emergency_contacts
        where id = p_contact_id and user_id = owner_id for update;
    if not found then return; end if;
    set constraints public.emergency_contacts_user_id_slot_key deferred;
    update public.emergency_contacts
        set slot = case when id = p_contact_id then 0 else chosen_slot end
        where user_id = owner_id and (id = p_contact_id or slot = 0);
    return query select * from public.emergency_contacts
        where id = p_contact_id and user_id = owner_id;
end;
$$;

create function public.remove_emergency_contact(p_contact_id uuid)
returns setof public.emergency_contacts
language plpgsql security invoker set search_path = '' as $$
declare
    owner_id uuid := auth.uid();
    removed public.emergency_contacts%rowtype;
begin
    if owner_id is null then
        raise insufficient_privilege using message = 'Authentication required';
    end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(owner_id::text, 0));
    delete from public.emergency_contacts
        where id = p_contact_id and user_id = owner_id returning * into removed;
    if not found then return; end if;
    if removed.slot = 0 then
        update public.emergency_contacts set slot = 0
        where id = (
            select id from public.emergency_contacts
            where user_id = owner_id order by slot limit 1
        ) and user_id = owner_id;
    end if;
    return next removed;
end;
$$;

revoke all on function public.make_primary_emergency_contact(uuid) from public, anon;
revoke all on function public.remove_emergency_contact(uuid) from public, anon;
grant execute on function public.make_primary_emergency_contact(uuid) to authenticated;
grant execute on function public.remove_emergency_contact(uuid) to authenticated;

commit;
