-- Hosted projects may have a pre-existing Auth signup trigger that inserts into
-- public.user_profiles. The backend deliberately omits NIC from Auth metadata.
-- Allow that trigger to insert a NULL NIC without aborting account creation.
-- This does not delete existing values or change profile access policies.
-- IF EXISTS keeps fresh projects without this optional profile table usable.
alter table if exists public.user_profiles
    alter column nic drop not null;
