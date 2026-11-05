# SOS: Call or send location by SMS

Tap SOS, then choose **Call** or **Send location**. The selected contact defaults
to slot 0; use the contact selector to choose another saved number.

- Call opens the phone handler for that number.
- Send location requests a GPS fix, creates a 30-minute share, then opens the
  phone's SMS composer with the recipient and fixed message filled in:

  `SOS: I may be in danger. Please contact me immediately. My latest shared location: <tracking link>`

The user reviews the draft and presses Send in Messages. Opening the composer
does not prove that a message was sent. No provider API or billing credentials
are needed; the phone's usual messaging plan/charges apply. No actual calls or
messages are made by the automated tests.

If the browser does not open Messages or preserve the message body, use **Open
SMS draft** again, or copy/select the message preview. SMS URI body handling
varies by phone/browser; test autofill on the actual iPhone/Android device.

## Phone setup

Location sharing requires HTTPS and browser location permission. The HTTP LAN
URL used for basic phone testing cannot provide location access on an iPhone.
Use a trusted HTTPS development URL or an HTTPS deployment that reaches the API.

Frontend `VITE_PUBLIC_APP_URL` optionally sets the public frontend origin used in
SMS links, for example `https://your-public-test-host.example`. Leave it blank to
use the current browser origin. It must point to the same running application
and backend as the sender. Restart Vite after editing frontend environment values.
An address on the computer's private Wi-Fi network is reachable only from that
network; contacts elsewhere need a public URL. No new backend credentials or
Supabase migrations are required for these prototype location shares.

Keep the SOS page open and return to it after sending. Location updates can pause
while Messages is in the foreground or the phone is locked. The initial GPS fix
is uploaded before opening the SMS, so the link has a position immediately.

## Tracking lifecycle and access

Authenticated endpoints under `/api/v1/emergency-location-shares`:

| Method/path | Body/result |
| --- | --- |
| POST | `{position: {latitude, longitude}, accuracy_m}` → initial shared position, token, timestamps (201) |
| PATCH `/{share_id}` | Same body; owner only |
| DELETE `/{share_id}` | Owner only; ends the link (204) |
| GET `/{share_id}` | Public, read-only, for anyone holding the unguessable link |

The API binds ownership to the verified Supabase user. The viewing link contains
only a random 32-byte share token, never an access token or owner key. It reveals
only the latest coordinates, reported accuracy, last-update time, and expiry.
Anyone receiving or forwarding the link can view it until it ends/expires.

The sender uploads changed positions at most once every 10 seconds. The recipient
page at `/track/:shareId` polls every five seconds, shows an accuracy circle, and
marks updates older than 30 seconds as paused. It does not claim a stale position
is current. Stop sharing revokes the link; leaving the SOS screen also attempts
to revoke it and always stops local GPS watching. If the revocation request fails,
the last received position can remain readable until expiry. Links expire after
30 minutes, and location watching stops locally at that deadline.

## Prototype storage limits

Shares live in backend process memory, matching the existing journey-sharing
prototype. Run **one Uvicorn worker**. Restart/reload/deploy invalidates existing
links, and multiple workers do not share them. There is one active share per
user, at most 1000 simultaneous shares, and expired entries are removed on access.
Production deployment needs a shared expiring store before scaling workers or
depending on restart continuity. This implementation does not persist a location
history or send SMS from the server.

## Manual verification

1. Log in with a real account and save two emergency contacts.
2. Open SOS: neither calling nor location sharing should start automatically.
3. Choose Call and check the selected number appears in the phone handler.
4. Return, choose a different contact, and select Send location. Allow location.
5. Verify Messages contains that recipient, the fixed text, and `/track/` link.
6. Send only to a consenting test contact. Open the link in a logged-out browser.
7. Return to the sender's SOS page and move; check updates and last-update time.
8. Stop sharing and check the recipient can no longer load the location.
9. Test denied location permission and an unavailable backend: no broken/empty
   tracking SMS should be opened, and Call should remain available.
