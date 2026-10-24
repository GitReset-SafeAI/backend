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

## Draft voice transcription

`POST /api/v1/reporting/transcriptions` requires the existing verified Supabase
identity before consuming the raw audio body. It returns a draft for review and
does not call report persistence, translation, classification, or account/NIC
workflows. Drafts and recordings are not saved in Supabase or on disk by this
feature. The final reviewed text is saved only after a separate report submission.
The backend cannot independently prove human review occurred.

WAV input is bounded to 10 MiB and 60 seconds, with a 30-second upload deadline
and at most two active uploads/transcriptions per process. Audio is temporarily
held in bounded application buffers and released on completion/failure; this is
not a guarantee of physical memory erasure or a transport-level buffer bound.
Unrelated container metadata is stripped before sending validated PCM audio to
Groq. Only audio and necessary model/language/format parameters are sent: no
account IDs, JWTs, coordinates, report IDs, or unrelated report context is added.

Audio may nevertheless reveal a speaker's identity, sensitive details, or
bystander speech. The transcript can contain these details too. User disclosure,
appropriate consent, provider retention/data-location controls, and operational
access must be agreed before real sensitive audio is sent. Backend non-retention
does not establish provider non-retention; consult
[Groq's current data policy](https://console.groq.com/docs/your-data) and the
organization's actual controls. No provider settings are changed by this feature.

Never log raw uploads, transcript text, authorization headers, credentials, or
provider request/response bodies, including in middleware and error tracing.
The API returns sanitized errors and never substitutes fabricated transcripts
or retries automatically. Responses contain only the draft, requested hint, and
`review_required: true`. They do not establish detected language, transcript
accuracy, an incident's truth, or emergency-service contact. Digital silence is
rejected, but non-speech noise may still produce plausible ASR text; user review
remains necessary. Sinhala/Tamil/mixed-speech recognition and existing English
translation accuracy require separate live evaluation with fictional recordings.

## Text reporting and translation

Report creation can send original incident text and the supplied language hint
to the configured Groq provider for English translation. No account/user ID,
JWT, coordinates, timestamps, or questionnaire fields are added to that request.
Personal details embedded in the description can still reach the provider;
provider data handling and appropriate user disclosure require review before
real sensitive reports are used. The original text remains unchanged in storage.
English reports bypass the provider. Legacy supplied translations for other
language hints remain accepted and are not independently verified.

Translation failures return a sanitized 503 before any report INSERT. There is
no untranslated fallback labelled as successful English, no retained provider
response, and no new translation status or pending record. Logs must not contain
original/translated descriptions, provider bodies, credentials, or token claims.
Language hints and model-reported success are not verified language detection or
proof of translation quality. Mocked tests do not validate Sinhala/Tamil accuracy.

The stored-report analysis operation requires verified authentication and loads
translation through a report-ID and verified-owner filter. It ignores caller
replacement text and treats missing/foreign reports identically. The privileged
read also checks returned ownership and does not expose report content through a
new endpoint. Existing analysis output remains provisional, not proof of events.

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

The optional authenticated [NIC-classification endpoint](nic-classification.md)
parses only the number's encoded indicator in memory. It does not persist the
NIC or result, contact a verification provider, update an account, or decide
eligibility. Its response explicitly states `identity_verified: false` and omits
the number and birth date. Report submission still does not require this step.
