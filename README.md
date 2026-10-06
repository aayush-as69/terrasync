# TerraSync

**Gemini-powered civic intelligence: from citizen complaint to verified resolution.**

Citizens report a civic problem (pothole, garbage, water leak...) with a photo, a typed or spoken description, and their GPS location. Flask sends that report to the Google Gemini API, which returns a structured ticket: category, severity, summary, civic risk, recommended department and a duplicate signal. TerraSync stores the ticket, routes it to the right department and officer, and tracks it through to citizen verification.

Built by team **BitBenders** for HackBase HackDays (Edition 2).

---

## Contents

- [Features](#features)
- [Status](#status)
- [How it works](#how-it-works)
- [Quickstart (local)](#quickstart-local)
- [Quickstart (Docker)](#quickstart-docker)
- [Demo walkthrough](#demo-walkthrough)
- [Configuration](#configuration)
- [Database and seed data](#database-and-seed-data)
- [Gemini civic intelligence](#gemini-civic-intelligence)
- [Issue lifecycle](#issue-lifecycle)
- [Voice reporting](#voice-reporting)
- [SMS notifications](#sms-notifications)
- [Authentication](#authentication)
- [API reference](#api-reference)
- [Photo uploads](#photo-uploads)
- [Deployment](#deployment)
- [Known gaps](#known-gaps)
- [Production checklist](#production-checklist)
- [Project layout](#project-layout)

---

## Features

| Area | What TerraSync does |
|---|---|
| **Reporting** | Voice, text, photo and GPS in one form. Spoken input becomes an editable transcript the citizen confirms before submitting. |
| **AI triage** | Gemini analyses photo + description + location and returns category, severity, summary, department, civic risk, detected language and a duplicate/similar-issue signal. |
| **Trust layer** | Gemini output is treated as untrusted: schema-constrained, re-validated, confidence-gated, and flagged for human review when unsure. Prompt-injection hardened. |
| **Resilience** | Primary Gemini model → fallback Gemini model → local deterministic fallback. Request timeout and a per-citizen hourly AI cost guard. A complaint is never lost. |
| **Routing** | Only a confident, non-duplicate AI ticket is auto-assigned to an available officer in the same ward. Everything else goes to the department queue for a human. |
| **Workflow** | Reported → Acknowledged → In Progress → Resolved → Verified, with a full status history per issue. |
| **Staff console** | Issue queue, assignment, officer stats, ward and department KPIs, SLA views, live map. |
| **Notifications** | SMS at each step of a complaint (console mode by default, Twilio supported). |
| **Auth** | Citizen phone + OTP or email + password; staff employee code + password + OTP. Server-side revocable sessions. |
| **Deployment** | Dockerfile, Docker Compose (MySQL 8 + app), Gunicorn, optional Caddy for automatic HTTPS. |

## Status

TerraSync is a **working prototype**. Be precise about what is and isn't proven:

| Item | State |
|---|---|
| Flask + MySQL backend, workflow, dashboards, voice, seed data | Implemented |
| Gemini triage + trust layer (validation, confidence gate, fallback model, timeout, cost guard) | Implemented. The trust layer is **unit-tested** (`tests/`); confirm it with your own key and database before a demo |
| Docker, Gunicorn, Caddy HTTPS config | Included, **not yet deployed to a cloud host** |
| SMS | Implemented. Defaults to `console` mode; **Twilio has not been tested against a live account** from this repo |
| Hinglish (mixed Hindi/English) voice | Not claimed. English (`en-IN`) and Hindi (`hi-IN`) only |

Demo logins are fake seeded accounts and are shared separately. For commit history see `git log`; this README describes what is implemented now, not a changelog.

## How it works

```
Citizen app (HTML/JS)                       Staff console (HTML/JS)
  voice / text + photo + GPS                  queue, assign, KPIs, map
            |                                         |
            +------------------+----------------------+
                               v
                  Flask REST API  (Gunicorn)
                  auth, workflow, routing, trust policy
                  |             |              |
                  v             v              v
          gemini_service.py   MySQL 8     SMS provider
          (analyse only)     (issues,     (console / Twilio)
           |  primary model   history,
           |  fallback model  KPI views)
           |  validate output
           v
        Gemini API (server-side key)
```

- The browser **never calls Gemini directly**. It calls `POST /api/issues`; Flask orchestrates Gemini and returns the structured result.
- **Gemini is an advisory layer.** `gemini_service.py` only analyses; it never touches the database. `app.py` decides what the result is allowed to change (see [Gemini civic intelligence](#gemini-civic-intelligence)).
- The `GEMINI_API_KEY` stays server-side. Never put it in HTML or JavaScript.
- Flask serves the static pages itself (`GET /` returns the landing page; `GET /<filename>` serves anything in `static/`), so no separate frontend server is needed.

## Quickstart (local)

Requires Python 3.12+ and MySQL 8.

**macOS / Linux**

```bash
cp env.example .env              # edit SECRET_KEY, DB_PASSWORD, GEMINI_API_KEY
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
mysql -u root -p < schema.sql
python seed_data.py              # 40 demo issues
flask --app app run --debug
```

**Windows (Command Prompt)**

```cmd
copy env.example .env
python -m venv venv
venv\Scripts\activate.bat
pip install -r requirements.txt
mysql -u root -p < schema.sql
python seed_data.py
flask --app app run --debug
```

Open `http://127.0.0.1:5000/`.

Run the unit tests (no database, network or API key needed):

```bash
python -m unittest discover -s tests -v
```

> **Windows note:** `mysql` must be on your `PATH`. If it isn't recognised, run it from the MySQL `bin` folder or use the full path, e.g.
> `"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p < schema.sql`

## Quickstart (Docker)

```bash
cp env.example .env     # set SECRET_KEY, DB_PASSWORD, DB_ROOT_PASSWORD, GEMINI_API_KEY
docker compose up -d --build
docker compose run --rm web python seed_data.py --reset    # 40 demo issues
# app: http://localhost:8000
```

See [Deployment](#deployment) for HTTPS and hosted use.

## Demo walkthrough

A reliable end-to-end run for judges (about 3 minutes):

1. **Citizen:** sign in with phone + OTP, tap **Speak** (or type), attach a pothole photo, allow GPS, submit.
2. **Gemini ticket:** the confirmation shows the AI-chosen department and severity. In the admin console the row has the Gemini badge, AI summary and department.
3. **Routing:** a confident, non-duplicate ticket is auto-assigned to an officer in that ward.
4. **Safety net:** submit a vague or off-topic report. It is still filed, flagged **Needs review**, and left in the department queue for a human.
5. **Workflow:** staff move the issue Acknowledged → In Progress → Resolved; the citizen confirms **Verified**. Each step is texted (console log unless Twilio is configured).

**"What if Gemini is wrong?"** Its output is validated against the database before use, low-confidence or non-civic results never override the citizen's choice and are never auto-assigned, and a human reviews flagged tickets. If Gemini is down, the complaint is still filed.

## Configuration

Copy `env.example` to `.env`.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session signing key. **Required outside dev**; the app refuses to start without one. |
| `FLASK_ENV` / `FLASK_DEBUG` | Dev mode switch. Also enables the dev-OTP shortcut (see [Authentication](#authentication)). |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | MySQL connection. `DB_NAME` defaults to `terrasync`. |
| `COOKIE_SECURE` | `false` in dev, `true` otherwise. Set explicitly to override. |
| `GEMINI_API_KEY` | Google Gemini API key (server-side secret). |
| `GEMINI_MODEL` | Primary Gemini model (default `gemini-3.8-flash`). Use an ID your key can access. |
| `GEMINI_FALLBACK_MODEL` | Tried automatically if the primary is unavailable, rate-limited or returns invalid output (default `gemini-3.5-flash-lite`). |
| `GEMINI_TIMEOUT_SECONDS` | Per-request timeout (default `25`). |
| `GEMINI_THINKING_LEVEL` | Optional `minimal` / `low` / `medium` / `high`. Lower is faster and cheaper. |
| `GEMINI_ENABLED` | `true` / `false`. When off, reports are filed with the local fallback and flagged for review. |
| `AI_AUTO_ASSIGN` | `true` (default): auto-assign confident, non-duplicate tickets. `false`: every ticket goes to the queue. |
| `AI_MAX_PER_CITIZEN_PER_HOUR` | Cost guard (default `10`). Beyond it, reports are still filed, without Gemini. |
| `DUPLICATE_RADIUS_METERS` | Radius for duplicate candidates around the report's GPS point (default `300`). |
| `SMS_PROVIDER` | `console` (prints to the server log) or `twilio`. |
| `SMS_NOTIFICATIONS_ENABLED` | Master switch for complaint SMS. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` / `TWILIO_MESSAGING_SERVICE_SID` | Twilio credentials (sender number or messaging service). |
| `DB_ROOT_PASSWORD`, `DOMAIN` | Docker Compose only: MySQL root password and the public domain for Caddy HTTPS. |
| `WEB_BIND` | Docker Compose only: interface the app's port 8000 is published on. Default `0.0.0.0`; set `127.0.0.1` when Caddy fronts the app so plain HTTP isn't reachable from outside. |

For anything beyond local dev, create a scoped DB user instead of using root:

```sql
CREATE USER 'terrasync_app'@'localhost' IDENTIFIED BY 'a-strong-password';
GRANT SELECT, INSERT, UPDATE, DELETE ON terrasync.* TO 'terrasync_app'@'localhost';
FLUSH PRIVILEGES;
```

## Database and seed data

```bash
mysql -u root -p < schema.sql
```

`schema.sql` creates the `terrasync` database with these tables: `citizens`, `citizen_notification_prefs`, `staff`, `departments`, `wards`, `issue_categories`, `issues`, `issue_status_history`, `sessions`, `citizen_otp_codes`, `staff_otp_codes`, `sms_log`. It also creates three views: `v_ward_stats` and `v_department_stats` (staff KPIs) and `v_issue_resolved_at` (helper). **It seeds no rows.**

> **MySQL 8 auth:** the default `caching_sha2_password` needs the `cryptography` package, which is already in `requirements.txt`.

### Migrations

A fresh database from `schema.sql` already includes everything. For an **existing** database, apply the numbered files in `migrations/` in order:

| File | Adds |
|---|---|
| `001_gemini_ai.sql` | AI columns on `issues` |
| `002_verified_status.sql` | `Verified` status; refreshes KPI views |
| `003_sms_log.sql` | `sms_log` table |
| `004_ai_review_guard.sql` | `ai_status`, `ai_needs_review`, `ai_duplicate_confidence` on `issues`; AI cost-guard index |

### Seeding demo data

```bash
python seed_data.py                                  # defaults: 15 staff, 120 citizens, 40 issues
python seed_data.py --reset                          # truncate first, then seed
python seed_data.py --issues 500 --citizens 200 --staff 20
python seed_data.py --export-xlsx seed_logins.xlsx   # also write logins to Excel
```

- Seeds departments, wards and categories that match what the frontend expects, then generates staff, citizens and issues across all categories and statuses.
- Every seeded account uses the password `password123`. Employee codes and phone numbers are randomised per run; use `--export-xlsx` or query directly: `SELECT employee_code, full_name, role FROM staff LIMIT 5;`
- Re-running **without** `--reset` upserts reference tables but adds more citizens, staff and issues each time. For a clean demo state, use `--reset`.

### Creating one staff account by hand

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt(12)).decode())"
```

```sql
INSERT INTO staff (full_name, employee_code, password_hash, role, phone, is_active)
VALUES ('Your Name', 'EMP-001', '$2b$12$...', 'super_admin', '+91XXXXXXXXXX', TRUE);
```

Staff roles: `field_officer`, `dept_head`, `super_admin`.

## Gemini civic intelligence

When a citizen submits a report, Flask sends photo + description + GPS context to Gemini and requests **constrained structured JSON**:

| Field | Meaning |
|---|---|
| category | One of the configured issue categories |
| severity | `high` / `med` / `low` |
| title, summary | Concise ticket headline and an officer-ready summary |
| department | Recommended responsible department |
| civic risk | e.g. traffic safety |
| detected language | Language of the report (English, Hindi, Hinglish text is understood) |
| is civic issue | `false` for spam, selfies, jokes, private disputes |
| duplicate of, duplicate confidence | An existing nearby open issue, if it is clearly the same problem |
| confidence | Model confidence in category + department |

### The trust model: "Gemini is advisory, TerraSync decides"

```
Report → Gemini (primary) → invalid/unavailable → Gemini (fallback) → still failing → local fallback
                │
        schema-constrained JSON
                ↓
   validate() against live DB values
                ↓
         confidence check (≥ 0.5)
          ┌─────┴─────┐
      confident     not confident / non-civic / AI down
          │               │
  AI category + severity   citizen's own choice kept
          │               + flagged "Needs review"
   duplicate?                   │
   ┌──┴──┐                       │
  no    yes ──────────────┐     │
   │                      ↓     ↓
 auto-assign        department queue (human assigns)
```

- **Constrained output.** The response schema's enums come from the live database (categories, departments, nearby issue codes), so the model can only answer with values TerraSync has.
- **Validated again.** `gemini_service.validate()` re-checks category, department, severity, a non-empty summary, that a duplicate code was really one of the candidates, and clamps confidence to 0-1.
- **Prompt-injection hardening.** The citizen's text is passed as quoted JSON data inside `<citizen_report>`, with `<` escaped so it can't forge a closing tag, and the system instruction says never to follow instructions inside it. Even if a model were fooled, its output still has to pass validation.
- **Confidence gate.** Below `MIN_CONFIDENCE` (0.5), the citizen's own category/severity are kept and the ticket is flagged `ai_needs_review`. Non-civic reports and failed/skipped analyses are also flagged.
- **Auto-routing only when safe.** A ticket is auto-assigned only if it is confident, not a duplicate, and `AI_AUTO_ASSIGN=true`. Otherwise it stays in the department queue, with the AI recommendation visible, and the admin console shows a **Needs review** badge.
- **Resilience.** Primary model, then fallback model (on 404/429/5xx or invalid output), then the local deterministic fallback. `GEMINI_TIMEOUT_SECONDS` bounds each call. `skipped` / `failed` / `done` is recorded in `issues.ai_status`; the API reports `status: "ok"` only when Gemini produced a validated ticket, so the demo never claims AI ran when it didn't.
- **Cost guard.** At most `AI_MAX_PER_CITIZEN_PER_HOUR` (default 10) Gemini analyses per citizen per hour; extra reports are still filed.

**Setup**

1. `pip install -r requirements.txt`
2. Existing database only: run migrations `001` and `004` (and `002`, `003` if not yet applied).
3. Set `GEMINI_API_KEY` in `.env` (server-side only). Optionally set the model, fallback model and timeout.
4. Start the app and file one real report; confirm the ticket shows the Gemini badge and `ai_model` is your model name rather than `fallback`.

**Tests** (no network, key or database needed):

```bash
python -m unittest discover -s tests -v
```

They cover schema re-validation, hallucinated duplicate codes, fallback model on 429, both-models-fail, timeouts, prompt-injection quoting, and the confidence / human-review policy.

## Issue lifecycle

```
Reported → Acknowledged → In Progress → Resolved → Verified
```

Valid status values (`VALID_ISSUE_STATUSES` in `app.py`): `Reported`, `Acknowledged`, `InProgress`, `Resolved`, `Verified`.

- `Verified` is final and can only be reached from `Resolved`, either by staff (admin "Mark verified") or by the reporting citizen via `POST /api/issues/<id>/verify` ("Yes, fixed" in the citizen app).
- `Verified` counts as closed everywhere `Resolved` does (KPIs, SLA, officer stats).
- Every transition is written to `issue_status_history`.

## Voice reporting

The report form has **Speak / Type** buttons. Speak uses the browser's Web Speech API (`SpeechRecognition`), so there is no new backend dependency and no audio upload.

Flow: **speak → transcript → edit → "Use This Report" → Description field → normal submission** (photo + text + GPS → Flask → Gemini). An unconfirmed transcript blocks submit, so nothing is sent by accident.

- Languages: English (`en-IN`) and Hindi (`hi-IN`). Mixed Hindi/English is not a separate recogniser; behaviour depends on the browser and should be tested before it is claimed.
- Browsers: Chrome, Edge, Safari. Unsupported browsers show a message and fall back to typing.
- **Needs HTTPS or `localhost`** for microphone access.
- In Chrome, the browser may send audio to its own speech service.

## SMS notifications

Citizens are texted at each step, using the phone number they signed in with.

| When | Example |
|---|---|
| Report received | `TerraSync: Your complaint #TS-1042 has been received. Category: Road Damage. Priority: High. Assigned to Roads.` |
| Assigned / reassigned | `TerraSync: Complaint #TS-1042 has been assigned to Roads.` |
| Status change | `... has been Acknowledged ...` / `... is now In Progress.` / `... marked Resolved. Please confirm in the TerraSync app ...` / `... is now Verified.` |

- **Providers:** `console` (default in dev; prints to the server log) and `twilio` (REST API, no extra package). The same provider sends login OTPs.
- **Best-effort and off the request path:** each message is written to `sms_log` (`queued` → `sent` / `failed`, or `skipped` if the citizen has no phone or turned notifications off) and delivered on a background thread. An SMS failure never breaks reporting or a status update.
- **Only real changes are texted.** Moving an issue back to `Reported` is not texted, and a citizen who verifies their own fix isn't texted about it.
- **Audit:** `GET /api/staff/sms-log` (`super_admin`, `dept_head`) shows the latest 100 messages with masked phone numbers.
- **India:** DLT sender/template registration is required before messages are delivered.
- If `SMS_PROVIDER` is unset or unrecognised outside dev, `send_sms()` raises an error rather than silently dropping messages.

## Authentication

**Citizen: phone + OTP**
1. `POST /api/citizen/send-otp` `{ phone }`: generates a 6-digit code, stores it in `citizen_otp_codes` (5-minute expiry), sends it via `send_sms()`, returns a masked number.
2. `POST /api/citizen/verify-otp` `{ phone, otp, display_name? }`: validates the code, creates the citizen on first login (passwordless signup), opens a session and sets an `httpOnly` `terrasync_session` cookie (30-day expiry).

**Citizen: email + password**
1. `POST /api/citizen/register` `{ name, email, phone?, password }`: bcrypt-hashes the password. `409` if the email or phone is taken.
2. `POST /api/citizen/login-email` `{ email, password }`: opens a session with the same cookie.

**Staff: employee code + password + OTP**
1. `POST /api/staff/login` `{ employee_code, password }`: checks the bcrypt hash, then sends an OTP to the staff member's registered phone.
2. `POST /api/staff/verify-otp` `{ staff_id, otp }`: opens a session with an `httpOnly` `terrasync_admin_session` cookie (12-hour expiry, `SameSite=Strict`).

Both flows use the shared `sessions` table, so any session can be revoked server-side:

```sql
DELETE FROM sessions WHERE citizen_id = ?;   -- or staff_id = ?
```

**Dev OTP shortcut.** With `FLASK_DEBUG=1` (or `FLASK_ENV=development`), the login endpoints include a `dev_otp` field in the response and print the code to the console, so no real SMS is needed. This is **off outside dev**. On a hosted demo with fake data it is acceptable, but anyone can then sign in as any phone number. For real use, set `FLASK_ENV=production`, unset `FLASK_DEBUG`, use `SMS_PROVIDER=twilio` and `COOKIE_SECURE=true`.

**OTP abuse limits.** `send-otp` enforces a 30 s resend cooldown, 5 sends per phone/staff account and 10 per IP per 15 minutes; `verify-otp` locks a code after 5 wrong guesses (`OTP_*` constants in `app.py`).

### Protecting new routes

```python
@app.get('/api/citizen/me')
@require_citizen
def citizen_me():
    return jsonify(g.citizen)

@app.get('/api/staff/queue')
@require_staff(roles=['field_officer', 'dept_head', 'super_admin'])
def queue():
    ...
```

`require_staff()` takes an optional list of allowed roles; omit it to require any authenticated staff member.

## API reference

### Citizen

| Method & path | Auth | What it does |
|---|---|---|
| `POST /api/citizen/send-otp` | none | Send a login OTP by phone. |
| `POST /api/citizen/verify-otp` | none | Verify OTP, create session (and citizen on first login). |
| `POST /api/citizen/register` | none | Email + password signup. |
| `POST /api/citizen/login-email` | none | Email + password login. |
| `POST /api/citizen/logout` | citizen | End the session. |
| `GET /api/citizen/me` | citizen | Current citizen's profile. |
| `PATCH /api/citizen/me` | citizen | Update `display_name` / `home_ward_id`. |
| `GET /api/wards` | none | List all wards. |
| `POST /api/issues` | citizen | File an issue; triggers Gemini triage and auto-routing. See below. |
| `GET /api/issues` | citizen | **My Reports**: the citizen's own issues, newest first, with officer and department. |
| `GET /api/issues/feed` | citizen | **Community feed / map**: city-wide issues, newest first (capped at 300). |
| `POST /api/issues/<id>/verify` | citizen (own) | Confirm a `Resolved` fix → `Verified` (400 if not Resolved, 403 if not theirs). |
| `GET /api/issues/<id>/history` | citizen (own) or staff | Status timeline for one issue. |

**`POST /api/issues` details**
- `ward_id` comes from the citizen's `home_ward_id`, never the request body.
- `issue_code` (e.g. `TS-2501`) is generated from the row's own AUTO_INCREMENT id via insert-then-update, avoiding a `SELECT MAX(...) + 1` race.
- Logs a `Reported` row to `issue_status_history`.
- Accepts `multipart/form-data` (when a photo is attached) or a plain JSON body.
- Response `201`: `{ issue_id, issue_code, ai }`, where `ai` has `enabled`, `status`, `model`, `category`, `severity`, `summary`, `department`, `risk`, `language`, `duplicate_signal`, `similar_issue_code`, `confidence`, `needs_review`, `routed_to`.
  - `ai.status` is `ok` only when Gemini produced a validated ticket; otherwise `failed` or `skipped` (and `model` is `fallback`).
  - `needs_review: true` means a human should check the category/department; the ticket is not auto-assigned.

### Staff

| Method & path | Auth | What it does |
|---|---|---|
| `POST /api/staff/login` | none | Employee code + password → sends OTP. |
| `POST /api/staff/verify-otp` | none | Verify OTP, create session. |
| `POST /api/staff/logout` | staff | End the session. |
| `GET /api/staff/me` | staff | Current staff profile. |
| `GET /api/staff/dashboard-kpis` | staff | City-wide totals by status and SLA, plus per-ward and per-department stats from `v_ward_stats` / `v_department_stats`. The admin app reads totals from here instead of recomputing client-side. |
| `GET /api/staff/issues` | staff | All issues joined with citizens, wards, categories and assigned staff, including AI fields (`ai_summary`, `ai_department`, `ai_confidence`, `ai_model`, `ai_needs_review`, `ai_status`). |
| `PATCH /api/staff/issues/<id>` | staff | Update `status` and/or `assigned_staff_id` (`null` explicitly unassigns; omitting the field leaves it unchanged). |
| `GET /api/staff/officers` | staff | Active staff with resolved counts and average resolution hours. |
| `GET /api/staff/sms-log` | super_admin, dept_head | Latest 100 SMS notifications (masked phone; sent / failed / skipped). |

**Role scoping for `PATCH /api/staff/issues/<id>`**
- `field_officer`: only issues already assigned to them; can never reassign or unassign.
- `dept_head`: unassigned issues or issues within their own department; can assign only to officers in that department.
- `super_admin`: unrestricted.

## Photo uploads

`POST /api/issues` saves an attached photo to `static/uploads/issues/<uuid>.<ext>` and stores the path in `issues.photo_url` (returned by `/api/issues`, `/api/issues/feed` and `/api/staff/issues`). Photos are served by the existing static route.

- Allowed: JPG, PNG, WEBP (checked by extension in `save_issue_photo()`).
- Size cap: 5 MB (`MAX_PHOTO_BYTES`).
- Filenames are randomised with `uuid4().hex`; the client's filename is never trusted or stored.
- **Local-disk storage is fine for a prototype, not production:** it won't survive a redeploy on most hosts, doesn't scale across instances, and has no content scanning. Move to S3/GCS, and consider re-encoding images to strip EXIF GPS data before serving them on the community feed.

## Deployment

> **Status:** packaged for Docker + Gunicorn but **not yet exercised on a cloud platform**. Treat the first deployment as something to verify.

| File | Purpose |
|---|---|
| `Dockerfile` | Python 3.12, non-root user, serves `app:app` with Gunicorn. |
| `gunicorn.conf.py` | 2 workers × 4 threads, 90 s timeout (for Gemini calls), trusts `X-Forwarded-*`. Tune with `WEB_CONCURRENCY`, `GUNICORN_THREADS`, `GUNICORN_TIMEOUT`, `PORT`. |
| `docker-compose.yml` | MySQL 8 (loads `schema.sql` on first start) + the app. DB and uploads live in named volumes. |
| `Caddyfile` | Optional reverse proxy with automatic HTTPS certificates. |

```
Internet → HTTPS (Caddy) → Gunicorn → Flask → Gemini / MySQL / SMS provider
```

**HTTPS is required** for microphone (voice reports) and GPS to work in browsers. Set `DOMAIN` in `.env`, point its DNS at the server, then:

```bash
docker compose --profile https up -d --build
```

Also set `COOKIE_SECURE=true`, and set `WEB_BIND=127.0.0.1` so the app is reachable only through Caddy (otherwise port 8000 stays open over plain HTTP).

**Hosted demo checklist**
1. `.env` has `SECRET_KEY`, `DB_PASSWORD`, `DB_ROOT_PASSWORD`, `GEMINI_API_KEY`, `DOMAIN`, `WEB_BIND=127.0.0.1`.
2. `docker compose run --rm web python seed_data.py --reset` → exactly 40 issues.
3. File one real report and confirm `ai.status` is `ok` (not `fallback`). Then test voice (English and Hindi) over HTTPS and the full flow: citizen report → Gemini ticket → admin acknowledge → in progress → resolve → citizen verify.
4. Decide on OTP mode: dev-OTP is fine for a fake-data demo; for real use switch to production mode with Twilio.
5. Send one real Twilio test message before describing SMS as live.

Photos are stored on a local volume. Move them to object storage before running more than one server.

## Known gaps

- **No votes, comments or citizen-confirmation tables.** Upvote counts and comment threads in the community feed are client-side only and reset on reload.
- **Twilio untested live.** Implemented, but not verified against a real account from this repo. The citizen app's notification toggle is UI-only; the backend honours `citizen_notification_prefs` but nothing in the app writes to it yet.
- **Rate limiting is basic.** OTP limits are DB-backed and per phone/IP. Password login (`/api/staff/login`) and `/api/citizen/register` are not throttled.
- **No cleanup job for expired `sessions` rows.** OTP rows are cleaned opportunistically on each `send-otp`, or via `flask --app app cleanup-otps` on a cron; `sessions` needs the same treatment.
- **Gemini is exercised only with fakes in tests.** The trust layer is unit-tested; a live call with your key and a database-backed end-to-end test of `POST /api/issues` are still to do.
- **Duplicates are flagged, not merged.** A likely duplicate is shown to staff with the matching issue code and is kept out of auto-assignment; there is no merge action yet.
- **No re-analyse action.** An issue filed while Gemini was down stays flagged for review; staff cannot yet re-run triage on it.
- **Auto-routing is simple.** It picks the first active officer in the recommended department and ward, preferring available over on-site; it does not balance workload.
- **Seeded accounts share the password `password123`.** Local dev and fake-data demos only.
- **Local-disk photo storage** (see [Photo uploads](#photo-uploads)).
- **Limited automated tests.** The Gemini trust layer has unit tests (`tests/`); the rest of the API (auth, workflow, SMS) is still checked manually. There are no DB-backed end-to-end tests yet.

### Planned refactors (not urgent)

- Split `app.py` (1,600+ lines) into Blueprints (`auth_bp`, `citizen_bp`, `staff_bp`). Pure reorganisation.
- Replace ad-hoc schema edits with a proper migration tool (Alembic), or at least keep extending the numbered `migrations/` files. Today `schema.sql` is the source for fresh installs and `migrations/` for upgrades; keep them in sync.
- Move the copy-pasted frontend logic (`CATS`, `catMap`, `catFor()`, sign-out handler) shared by the admin and citizen apps into a plain `common.js`.
- Add endpoint tests against a test database (the Gemini trust layer is already unit-tested).

## Production checklist

- [ ] Deploy to a cloud host with a public HTTPS URL (Docker/Gunicorn/Caddy files are ready). Set `WEB_BIND=127.0.0.1` when using Caddy.
- [ ] Apply migrations through `004` on any existing database.
- [ ] Set `GEMINI_API_KEY`, confirm `GEMINI_MODEL` / `GEMINI_FALLBACK_MODEL` are IDs your key can access, then file one report and check `ai_model` is not `fallback`.
- [ ] Watch `ai_status` and `ai_needs_review` (e.g. a rising share of `failed`) as an early signal that the key, model or quota has a problem.
- [ ] Set `SMS_PROVIDER=twilio` and the `TWILIO_*` variables, complete India DLT registration, and send a real test message.
- [ ] Set `FLASK_ENV=production`, unset `FLASK_DEBUG` (disables dev-OTP), keep `COOKIE_SECURE=true`.
- [ ] Put login and OTP endpoints behind a proper rate limiter (`Flask-Limiter` or an edge/WAF layer), including password login.
- [ ] Cron `flask --app app cleanup-otps` and `DELETE FROM sessions WHERE expires_at < NOW()`.
- [ ] Use the scoped `terrasync_app` MySQL user, never root.
- [ ] Reset the database and set real bcrypt hashes for all accounts; do not ship `seed_data.py` accounts.
- [ ] Move issue photos off local disk to object storage.
- [ ] Add votes/comments/confirmation tables if those citizen-app features must be real.
- [x] Run under Gunicorn, not the Flask dev server (`gunicorn.conf.py`, `Dockerfile`).

## Project layout

```
app.py                  Flask app: auth, API, workflow, routing, trust policy, SMS
gemini_service.py       Gemini analysis service (validation, fallback model, timeout); no DB access
tests/                  Unit tests for the Gemini trust layer
schema.sql              Full schema for fresh databases
migrations/             001 Gemini AI · 002 Verified · 003 SMS log · 004 AI review/cost guard
seed_data.py            Demo data generator (default 40 issues)
static/                 Landing page, citizen app/login, admin app/login, uploads/
Dockerfile              Container image (Gunicorn)
docker-compose.yml      MySQL 8 + app (+ optional Caddy)
gunicorn.conf.py        Gunicorn settings
Caddyfile               HTTPS reverse proxy config
env.example             Environment variable template
GEMINI_SETUP.md         Gemini setup notes
requirements.txt        Python dependencies
```

| Page | File |
|---|---|
| Landing | `static/terrasync-opening.html` |
| Citizen sign-in | `static/terrasync-citizen-login.html` |
| Citizen app | `static/terrasync-citizen-app.html` |
| Staff sign-in | `static/terrasync-admin-login.html` |
| Staff console | `static/terrasync-admin-app.html` |
