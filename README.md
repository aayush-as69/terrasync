# TerraSync

**Gemini-assisted civic intelligence for turning citizen reports into routed, trackable and citizen-verified civic issues.**

TerraSync lets a citizen submit a civic complaint with text or voice, a photo and GPS context. Flask coordinates authentication, workflow, routing, notifications and AI analysis. Gemini produces a structured recommendation, while a server-side trust layer validates that recommendation before any automated routing is allowed.

Built by **BitBenders** for HackBase HackDays (Edition 2).

## Why TerraSync?

Civic complaints are difficult to route consistently: descriptions are incomplete, photos add context, the responsible department varies by issue, and automated classification can be wrong.

TerraSync treats AI as an **advisory layer**, not the system of record:

**Citizen report → AI analysis → validation → confidence/duplicate gates → human or automatic routing → resolution → citizen verification**

If Gemini is unavailable or uncertain, the complaint is still stored and routed for human review.

## Core capabilities

| Area | Implemented capability |
|---|---|
| Citizen reporting | Text, browser voice input, photo upload and GPS |
| AI triage | Gemini category, severity, summary, department, civic risk, language and duplicate signal |
| AI safety | Schema validation, live-value validation, confidence gate, prompt-injection hardening and fallback |
| Routing | Ward/department routing with safe auto-assignment only for confident, non-duplicate tickets |
| Workflow | Reported → Acknowledged → In Progress → Resolved → Verified |
| Staff console | Queue, assignment, KPIs, SLA views and map |
| Notifications | Console SMS provider plus optional Twilio integration |
| Authentication | Citizen OTP/password flows and staff password + OTP |
| Resilience | Primary model → fallback model → deterministic local fallback |
| Deployment | Docker, Gunicorn and optional Caddy HTTPS configuration |

## Architecture

```text
Citizen web app / Staff console
            │
            ▼
       Flask API
  auth · workflow · routing
  trust policy · notifications
       │       │       │
       ▼       ▼       ▼
    Gemini    MySQL    SMS
  analysis      │    console/Twilio
       │        │
       └── validation ──┐
                        ▼
                 Issue lifecycle
       Reported → Acknowledged → In Progress
                    → Resolved → Verified
```

The browser never calls Gemini directly. The server owns the Gemini key and decides what model output is allowed to change.

## AI trust model

Gemini output is untrusted input.

1. The report is sent to the configured Gemini model.
2. The response is constrained to the application's expected structure.
3. The response is validated against live database categories, departments and duplicate candidates.
4. Confidence is checked.
5. Non-civic, low-confidence, duplicate or failed analyses are kept out of automatic assignment.
6. A deterministic fallback keeps the complaint workflow available when AI is unavailable.

See [AI documentation](docs/AI.md).

## Quick start

### Local

Requirements: **Python 3.12+** and **MySQL 8**.

```bash
cp env.example .env
pip install -r requirements.txt
mysql -u root -p < schema.sql
python seed_data.py
flask --app app run --debug
```

Open **http://127.0.0.1:5000**.

Run the offline unit tests:

```bash
python -m unittest discover -s tests -v
```

### Docker

```bash
cp env.example .env
docker compose up -d --build
docker compose run --rm web python seed_data.py --reset
```

Open **http://localhost:8000**.

## Configuration

Copy `env.example` to `.env`. Important settings include:

- `SECRET_KEY` — server session signing key
- `DB_*` — MySQL connection
- `GEMINI_API_KEY` — server-side Gemini secret
- `GEMINI_MODEL` / `GEMINI_FALLBACK_MODEL` — AI providers
- `GEMINI_TIMEOUT_SECONDS` — per-call timeout
- `GEMINI_ENABLED` — enable/disable Gemini analysis
- `AI_AUTO_ASSIGN` — allow safe automatic assignment
- `AI_MAX_PER_CITIZEN_PER_HOUR` — AI cost guard
- `SMS_PROVIDER` — `console` or `twilio`
- `TWILIO_*` — Twilio credentials when live SMS is enabled

Never put Gemini, database or Twilio secrets in frontend JavaScript.

## Database and migrations

Fresh installs use:

```bash
mysql -u root -p < schema.sql
```

Existing databases should apply the numbered migrations in order:

- `001_gemini_ai.sql`
- `002_verified_status.sql`
- `003_sms_log.sql`
- `004_ai_review_guard.sql`

The seed script creates demo citizens, staff, departments, wards, categories and issues. Demo credentials are for local demonstration only and must not be reused for production.

## Authentication and security

Citizen authentication supports phone OTP and email/password. Staff authentication uses employee code, password and OTP. Sessions are server-side and revocable.

Important security boundaries:

- Gemini keys remain server-side.
- Model output is validated before routing.
- Low-confidence AI does not silently override citizen input.
- Staff routes are permission checked.
- Demo/dev shortcuts must not be treated as production authentication.
- Uploaded photos should move to controlled object storage before a public deployment.
- Production deployments should use HTTPS, rate limiting and a scoped database account.

See [Security](docs/SECURITY.md).

## Voice reporting

The citizen form uses the browser Web Speech API.

**Speak → review transcript → edit if needed → submit normally**

Supported configurations documented by the project are English (`en-IN`) and Hindi (`hi-IN`). Mixed Hindi/English is not claimed as a separate recognizer.

Microphone access normally requires HTTPS or localhost.

## SMS

SMS notifications support a console provider for development and an optional Twilio provider.

Production use in India requires the relevant sender/template registration and live provider verification. SMS failures are designed not to break complaint creation or status transitions.

## Testing

The repository includes offline unit tests for the AI trust layer, including validation, fallback behavior, timeout handling, prompt-injection quoting and confidence/review policy.

For a full demo verification, test:

1. Citizen login.
2. Text report with photo/GPS.
3. Voice report.
4. Gemini analysis and validation.
5. Low-confidence/fallback path.
6. Staff assignment.
7. Status transitions.
8. Citizen verification.
9. SMS console/Twilio path when configured.

See [Testing](docs/TESTING.md).

## Known limitations

TerraSync is a **working prototype**, not a production municipal platform.

- Cloud deployment is not implied by the included Docker/Caddy configuration.
- Live Twilio delivery requires a configured and verified account.
- Browser speech recognition depends on browser support and HTTPS/localhost.
- Issue photos are currently local-file oriented and should use object storage for production.
- The current Flask application is monolithic and can be split into Blueprints as a future refactor.
- Endpoint coverage against a real test database should be expanded beyond the existing trust-layer tests.
- Production deployments need stronger rate limiting, monitoring, backups and operational controls.

## Documentation

| Guide | Purpose |
|---|---|
| [Documentation index](docs/README.md) | All technical guides |
| [AI](docs/AI.md) | Gemini trust layer and fallback |
| [Security](docs/SECURITY.md) | Auth, secrets and threat boundaries |
| [Testing](docs/TESTING.md) | Test and demo verification |
| [Setup](docs/SETUP.md) | Environment and database setup |
| [API](docs/API.md) | Endpoint inventory |
| [Deployment](docs/DEPLOYMENT.md) | Docker/Gunicorn/Caddy deployment |
| [Operations](docs/OPERATIONS.md) | Database, SMS and operational procedures |

## Project status

**Functional prototype / hackathon project**

The strongest architectural feature is the separation between AI recommendation and application authority: Gemini can suggest a classification, but the server validates it and decides whether it is safe to automate.

**Team:** BitBenders  
**Project:** TerraSync  
**Hackathon:** HackBase HackDays — Edition 2