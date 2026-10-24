# Automatic incident analysis

`POST /api/v1/reports` now saves the owned report, then awaits the same analysis
pipeline used by `POST /api/v1/incident-ai/analyze`. No localhost HTTP call is made.
The saved report ID and saved English translation are passed to the analysis
service, which inserts the result into `incident_ai_analyses`.

The report's supplied lighting is mapped to the analysis lighting enum. When
`occurred_at` is provided, its time is interpreted in Sri Lanka time (UTC+05:30)
and mapped to the existing five time periods. Without those details, the LLM
extracts them from the report. Submission time is not used as incident time.

The report endpoint still returns HTTP 201 with the existing report response.
It now waits for classification and analysis storage, so response time includes
the provider request. Provider failures produce the existing needs_review
fallback, which is also saved. If analysis storage fails, the committed report
is preserved, a sanitized warning is logged, and creation still returns 201.
Retry analysis through the authenticated manual endpoint using that report ID;
do not resubmit the report. There is no automatic retry queue.

Configure Groq and Supabase analysis service-role credentials as for the manual
analysis endpoint. No database migration is required for this integration.
