# Initial privacy and safety principles

- Collect only information needed for an explicit purpose. Define retention and
  deletion rules before collecting incident data.
- Restrict identifying information to authorised roles. Future public responses
  and analytics should use suitable aggregation and avoid exposing identities or
  precise incident details that could identify a reporter.
- Treat voice recordings as sensitive: minimise collection and retention, obtain
  appropriate consent, control access, and review transcription-provider handling
  before enabling uploads.
- Keep service-role credentials and LLM API keys on the backend. Never log secrets,
  report text, or recordings. No authentication or access controls exist yet;
  they must be designed before sensitive endpoints are introduced.
- Reported-incident hotspots are incomplete, potentially biased and unverified
  evidence. They are not ground truth or an objective measure of danger. Few or
  absent reports do not establish safety.
- Future AI classifications and anomaly flags require cautious interpretation and
  human review; they are not proof of an incident or a fraudulent report.
- Route recommendations must communicate uncertainty and never guarantee safety.

This scaffold collects no reports, recordings, or personal information and makes
no connections to external data services.
