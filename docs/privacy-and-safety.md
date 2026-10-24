# Initial privacy and safety principles

- Collect only information needed for an explicit purpose. Define retention and
  deletion rules before collecting incident data.
- Restrict identifying information to authorised roles. Future public responses
  and analytics should use suitable aggregation and avoid exposing identities or
  precise incident details that could identify a reporter.
- Treat voice recordings as sensitive: minimise collection and retention, obtain
  appropriate consent, control access, and review transcription-provider handling
  before enabling uploads.
- Keep secret/service-role credentials and LLM API keys on the backend. Never log
  secrets, access tokens, JWT claims, report text, or recordings. Report creation
  derives ownership from a verified Supabase token; client-supplied IDs and owner
  headers are not accepted as verified identity.
- Reported-incident hotspots are incomplete, potentially biased and unverified
  evidence. They are not ground truth or an objective measure of danger. Few or
  absent reports do not establish safety.
- Future AI classifications and anomaly flags require cautious interpretation and
  human review; they are not proof of an incident or a fraudulent report.
- Route recommendations must communicate uncertainty and never guarantee safety.
- Optional lighting, crowd, and current-safety fields are private, self-reported
  report context and must not appear in public map responses. Current safety is
  stated by the user when reporting, independent of AI severity. Selecting
  `MAY_STILL_BE_IN_DANGER` does not contact emergency services or trigger
  notifications, dispatch, or additional location tracking.

The creation repository binds ownership to a trusted backend identity and stores
only the agreed report fields. Its privileged Data API access bypasses RLS;
database grants and backend ownership enforcement remain separate controls.
The returned `ReportResponse` omits `user_id`. No public report listing or
retrieval is implemented. Validation errors omit input values and context, and
storage failures return a generic message without provider error details.

Invalid authentication is rejected before persistence; verification outages fail
closed. The backend retains only the UUID identity needed for ownership, not
token metadata. This provides public anonymity, not technical unlinkability:
internally reports remain linked to Supabase accounts. Tests use generated
signing keys, synthetic data, and mocked network responses. No raw audio is
accepted or retained by the text-based creation endpoint, and no live Supabase
project has been used for these checks.

NIC collection/verification is separate from Supabase authentication and is not
required for report submission. No NIC registration flow, provider, storage,
or verification has been implemented. Do not place NICs in Auth metadata/JWT
claims, reports, public responses, logs, or analytics. Full NIC retention is not
approved; format checks would not prove identity, and plain unsalted hashes
would not anonymise NICs. See [registration decisions](authentication.md#nic-registration-is-a-separate-unresolved-integration)
for the unresolved purpose, provider, access, protection, and retention decisions.
