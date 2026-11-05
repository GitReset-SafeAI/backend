# Emergency contacts

After login, Emergency has an **Add a contact** button. The form accepts a name,
Sri Lankan phone number, and optional relationship. The browser normalizes local
numbers such as `077 123 4567` to `+94771234567`. Saved contacts appear on Emergency
as call links. Account > Emergency contacts also supports editing, deleting,
and selecting the primary contact. SOS shows **Call** and **Send location**, with
slot 0 selected by default. Send location opens a prefilled SMS for the user to
review and send; see [SMS and tracking setup](emergency-sms.md).
Adding or saving a contact does not call anyone or send a message.

## Required Supabase setup

1. Open SQL Editor in the same Supabase project as the frontend/backend.
2. Paste and run the entire file
   `supabase/migrations/20261104000200_create_emergency_contacts.sql` once.
   This creates the private table, ownership policies, and atomic add function.
   Alternatively, apply it through your existing migration deployment workflow.
3. Keep the backend's existing `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, and
   `SUPABASE_JWKS_URL`. No new environment keys are required.
4. Restart the backend on port 8001 and the frontend development server. Log in,
   open Emergency, choose Add a contact, and save a number you control.
5. Refresh the page to verify persistence. In Account, edit it, select a primary
   contact, and remove a contact. With a second account, verify its list is empty
   and it cannot change the first account's contact IDs.

No live database migration or phone call was performed during implementation.
Run a call only when you deliberately want to open your phone's dialer.

## API contract

All paths begin `/api/v1/emergency-contacts` and require a valid Supabase
`Authorization: Bearer <access_token>` header:

| Method/path | Body | Result |
| --- | --- | --- |
| GET | none | Ordered contact array |
| POST | `{name, phone, relationship?}` | Created contact (201) |
| PATCH `/{id}` | `{name, phone, relationship?}` | Updated contact |
| DELETE `/{id}` | none | Empty 204 |
| POST `/{id}/primary` | none | Empty 204 |

Contacts have `{id, name, phone, relationship?}`. Empty relationships are omitted.
Unknown/inaccessible IDs return 404, invalid input 422, the five-contact limit
409, missing/invalid authentication 401, and unavailable storage 503. Private
successful responses use `Cache-Control: no-store`.

The backend verifies the user's JWT before opening storage. It forwards that
user token with the publishable key, so Supabase RLS also enforces ownership.
No privileged secret/service-role key is used for contacts. Reads and changes
add an explicit verified-owner filter; client owner/priority fields are rejected.
Returned owner IDs are checked and are never exposed in the API response.

Five unique numbered slots enforce the per-user limit at database level. The
add function serializes slot allocation per owner. Contact ordering uses the most
recent primary selection, then creation time and ID. Removing the primary contact
automatically leaves the next contact first. Reusing a vacant slot does not move
a newly added contact ahead of an existing primary.

Frontend contact caches are keyed by user and cleared on identity changes.
Real accounts always use the backend contact adapter, even when unrelated data
features use mock mode. Demo accounts keep their existing local-only behavior.

## Verification

`tests/test_emergency_contacts.py` uses mocked Supabase HTTP responses to verify
ownership filters, authenticated storage headers, CRUD, limits, failures, and
redacted validation. The web contact transport and Supabase-auth tests verify
adding a number directly from Emergency, normalization, persistence after remount,
save failures, authentication, and adapter isolation. These tests do not execute
the SQL migration or establish live RLS behavior; complete the live checks above.
