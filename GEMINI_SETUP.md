# TerraSync: Gemini setup

How TerraSync uses Gemini, how to turn it on, and how to confirm it is really running. For installing and running the whole app, see `README.md`.

**Flow:** Citizen → Flask REST API → `gemini_service.py` (analyse + validate) → trust policy in `app.py` → structured civic ticket → auto-routing or human review → Admin / Citizen tracking.

> **Gemini is advisory. TerraSync decides.** The model's output is treated as untrusted and validated before it can affect the civic workflow.

## What Gemini receives and returns

Flask sends Gemini:

- the photo, when one is attached
- the citizen's title and description (typed, or spoken and confirmed), as quoted data
- location text, ward and GPS context
- nearby open issues (within `DUPLICATE_RADIUS_METERS`) for the duplicate check
- the allowed categories and departments from the live database

Gemini returns structured JSON: `is_civic_issue`, `category`, `severity`, `title`, `summary`, `risk`, `department`, `confidence`, `duplicate_of`, `duplicate_confidence`, `detected_language`.

## Safety and reliability layers

| Layer | What it does |
|---|---|
| Constrained schema | Category, department and duplicate-code enums come from the database, so the model can only choose real values. |
| Post-response validation | `validate()` re-checks every field, rejects empty summaries, ignores duplicate codes that weren't candidates, clamps confidence to 0-1. |
| Prompt-injection hardening | Citizen text is quoted JSON inside `<citizen_report>` with `<` escaped; the system instruction says to analyse it, never obey it. |
| Confidence gate | Below 0.5 the citizen's own category/severity are kept and the ticket is flagged for human review. |
| Safe auto-routing | Auto-assign only when the ticket is confident, civic and not a duplicate. Otherwise it waits in the department queue. |
| Fallback model | If the primary model is unavailable (404/429/5xx) or returns invalid output, `GEMINI_FALLBACK_MODEL` is tried. |
| Local fallback | If Gemini fails entirely, the complaint is still filed using the citizen's choices and rule-based defaults, flagged for review. |
| Timeout | `GEMINI_TIMEOUT_SECONDS` stops a slow call from hanging the report. |
| Cost guard | `AI_MAX_PER_CITIZEN_PER_HOUR` limits Gemini calls per citizen; extra reports are still filed. |

## Setup

1. Install dependencies: `pip install -r requirements.txt`
2. Create `.env` from `env.example` and set:

   ```env
   GEMINI_API_KEY=your-key
   GEMINI_MODEL=gemini-3.8-flash
   GEMINI_FALLBACK_MODEL=gemini-3.5-flash-lite
   GEMINI_TIMEOUT_SECONDS=25
   ```

   The key is a **server-side secret**. Never put it in the HTML or JavaScript files. Use model IDs your key can access (see Google's model list).
3. Database:
   - Fresh database: `schema.sql` already includes everything.
   - Existing database: run `migrations/001_gemini_ai.sql` and `migrations/004_ai_review_guard.sql` (plus `002`, `003` if not yet applied).
4. Run the app (`flask --app app run --debug`, or Docker; see `README.md`).

## Confirm Gemini is actually running

Submit one real report and check the response and the admin queue:

- **Gemini ran:** `ai.status` is `ok`, the ticket shows the Gemini badge and AI summary, and `ai_model` holds a real model name (the fallback model's name if the primary failed).
- **Needs review:** the ticket shows a **Needs review** badge when confidence was low, the report wasn't a civic issue, or AI was unavailable. It is not auto-assigned.
- **Fallback ran:** `ai_model` is `fallback` and `ai_status` is `failed` or `skipped`. Typical causes: missing/invalid key, a model ID your key can't access, a timeout, or the hourly cost guard.

**Do this check before any demo.** A wrong model ID looks like a working app that just isn't using AI.

## Tests

```bash
python -m unittest discover -s tests -v
```

No network, API key or database needed. They cover schema re-validation, hallucinated duplicate codes, fallback on 429, both-models-fail, timeouts, prompt-injection quoting and the confidence / human-review policy.

## Demo scenario

Submit (by voice or text):

> There is a huge pothole outside the college gate. Bikes are falling here at night.

Attach a pothole photo and allow GPS. With Gemini enabled, expect roughly: category roads / road damage, High priority when the evidence supports a safety risk, risk traffic safety, department Roads (the seeded department name), status Reported. A confident, non-duplicate ticket is auto-routed to an eligible officer in that ward.

To show the safety net, submit a vague or off-topic report: it should be filed but flagged **Needs review** and left unassigned.

## Local development notes

- Citizen OTPs are 6 digits and are printed in the Flask console in dev mode, for example:

  ```text
  [DEV SMS to +91XXXXXXXXXX]: Your TerraSync verification code is 123456. It expires in 5 minutes.
  ```
- Authentication timestamps are stored and compared in UTC. The MySQL connection sets its session timezone to `+00:00`, so OTP and session expiry don't depend on the server's local timezone (relevant on Windows).
- Voice and GPS need `localhost` or HTTPS in the browser.
