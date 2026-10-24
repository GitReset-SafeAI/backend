# SafeRoute AI Backend

FastAPI foundation for SafeRoute AI, a planned harassment reporting, hotspot
detection, and safety-aware route recommendation application for Sri Lanka.
Health and authenticated report-creation routes are implemented. Live Supabase
signing configuration and persistence still need verification. See the original
[project overview](README) for the wider product context.

## Stack and prerequisites

- Python 3.12+ (`.python-version` selects 3.12 for CI).
- FastAPI, Uvicorn, Pydantic, and pydantic-settings.
- HTTPX for server-side Supabase Data API access and mocked HTTP tests.
- PyJWT with cryptography for asymmetric Supabase access-token verification.
- pytest for tests; Ruff for linting and formatting.
- Supabase/PostgreSQL migrations; no database or Supabase SDK is needed for normal tests.
- GitHub Actions for checks on pushes and pull requests.

Install Python with pip and venv support, then run commands from the repository root.
Dependencies are declared only in `requirements.txt`; `pyproject.toml` configures tools.

## Local setup

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Linux/macOS:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` locally as needed. Environment variables take precedence over `.env`.
`APP_ENV` defaults to `development`; `APP_VERSION` defaults to `0.1.0`. Supported
environments are `development`, `test`, `staging`, and `production`. Supabase and
LLM settings are optional and may remain blank. The application also runs without
an `.env` file. Restart the process after changing settings.

Never commit real credentials. Supabase service-role and LLM keys belong only on
the backend and must never appear in the mobile client or logs.

## Run

### Audio conversion runtime

The draft transcription endpoint accepts raw WAV, M4A/MP4 AAC, and MP3 uploads.
Compressed formats require **both `ffmpeg` and `ffprobe` on the backend PATH**;
WAV requests continue working without them. No Python audio package is needed.
Use a maintained build with MOV/MP4 and MP3 demuxers, AAC/MP3 decoders, and the
PCM signed 16-bit encoder/resampler. Format support is deliberately restricted;
WebM and other codecs/containers are not enabled.

Windows: obtain a build from the Windows links on the
[official FFmpeg download page](https://ffmpeg.org/download.html), verify the
distributor's checksum, extract outside this repository, and add its `bin`
directory (containing both executables) to your user/service PATH. Restart the
terminal/backend after changing PATH. Verify with:

```powershell
ffmpeg -version
ffprobe -version
```

Ubuntu/Debian deployment and CI:

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends -y ffmpeg
ffmpeg -version
ffprobe -version
```

There is no container/deployment manifest in this repository. Install the runtime
on the actual backend host; Python requirements alone do not provide it. CI
installs FFmpeg and requires generated-media integration tests rather than skips.
Local conversion tests skip when tools are absent; set `SAFEROUTE_REQUIRE_FFMPEG=1`
in the test environment to require them. Never commit binaries or recordings.

Conversion uses server-controlled arguments with no shell, normalizes to mono
16 kHz PCM WAV, and shares the endpoint's two process-wide slots. Its independent
`TRANSCRIPTION_CONVERSION_TIMEOUT_SECONDS` defaults to 15 seconds (maximum 30).
The backend service account needs private writable temporary storage. Input
files are removed after decoding or failure; output is bounded in memory. See
[API contracts](docs/api-contracts.md#draft-voice-transcription-post-apiv1reportingtranscriptions)
and [privacy guidance](docs/privacy-and-safety.md#draft-voice-transcription).

### Start the backend

```bash
uvicorn app.main:app --reload
```

- Health: http://127.0.0.1:8000/api/v1/health
- Swagger UI: http://127.0.0.1:8000/docs
- OpenAPI: http://127.0.0.1:8000/openapi.json

Documentation endpoints are enabled only with `APP_ENV=development`. The health
endpoint reports process health without contacting external services:

```json
{"status": "ok", "service": "saferoute-ai-backend", "version": "0.1.0"}
```

## Checks

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Apply formatting with `python -m ruff format .`. Tests use an isolated environment
without credentials or external network requests. CI runs the same checks.

## Project organisation and planned work

`app/main.py` creates the application and registers versioned routers.
`app/core` holds settings and token verification; `app/api` holds routes
and reusable dependencies. `app/schemas`, `app/services`, `app/repositories`, and
`app/utils` provide boundaries for shared schemas, business services, database
access, and utilities as features are added. Supabase migrations belong in
`supabase/migrations`; `seed.sql` currently inserts nothing.

The reports migrations define reporting storage, nullable context fields, and
creation privileges with RLS enabled and ordinary client access withheld. The
creation repository uses the configured Supabase HTTPS URL and modern secret
key, separately from JWT verification through public JWKS. Report ownership comes
only from the verified token subject. See [authentication](docs/authentication.md)
for supported signing mode and registration/NIC decisions, and
[reporting database notes](docs/database.md) for constraints and the local SQL check.

Language classification, severity assessment, anomaly detection, hotspot analysis,
and route comparison are planned and unimplemented. No database connection or
authentication is established at startup. The report endpoint and its
401/403/503 error behaviour are documented in [API contracts](docs/api-contracts.md).

For parallel development, agree on contracts in [API contracts](docs/api-contracts.md),
keep feature changes focused in their layers, and run checks before opening a PR.
Review [privacy and safety](docs/privacy-and-safety.md) before introducing sensitive
data and [sample-data guidance](sample-data/README.md) before adding demo fixtures.

## License

Released under the [MIT License](LICENSE).
