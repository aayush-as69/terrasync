# TerraSync Operations

## Database

Fresh installations use schema.sql. Existing databases use the numbered migration files. Keep schema and migration changes synchronized when adding new workflow or AI fields.

## AI operations

Monitor AI success/failure status, review rate, fallback usage, duplicate signals and per-citizen AI usage. A sudden increase in fallback or failed analyses can indicate provider, quota, configuration or prompt/schema problems.

## SMS operations

Development uses the console provider by default. Live Twilio operation requires credentials and the applicable India sender/template registration. SMS is best-effort and should not roll back complaint creation or status transitions.

## Seed data

Seeded data is for demos. Use --reset for a clean repeatable demo state. Never use demo passwords or seeded accounts in a production database.

## Incident response

If an API key or credential is exposed, rotate it immediately, remove it from tracked files/logs, review recent access, replace the affected environment configuration and rerun relevant tests.

If Gemini behaves unexpectedly, disable GEMINI_ENABLED or AI_AUTO_ASSIGN while investigating; complaints can continue through the human-review path.
