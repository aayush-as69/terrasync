# TerraSync — Vercel deployment checklist

This package is arranged with `app.py` and `requirements.txt` at the repository root for Vercel's Flask auto-detection. It stays on Python/Flask + MySQL + HTML/CSS/vanilla JavaScript; there is no Node.js frontend build step.

## 1. Create and configure the database

1. Provision a hosted MySQL 8 database that accepts connections from your Vercel deployment.
2. Run `schema.sql` against it. If upgrading an existing database, apply any missing migrations in `migrations/` in numeric order through `004`.
3. Optionally run `python seed_data.py` locally if you want demo data. Do not use seeded demo credentials for real users.
4. Copy the database host, port, database name, username and password into Vercel Environment Variables. Enable TLS/SSL in your database provider where supported and configure its network access rules.

## 2. Import into Vercel

1. Push the contents of this ZIP to a GitHub repository (the files inside this ZIP should be at the repository root).
2. In Vercel, choose **Add New → Project**, import that repository, and leave **Root Directory** at `./`.
3. Let Vercel detect Flask/Python. Do not add an npm install command or a frontend build command.
4. Configure the environment variables below before deploying.

## 3. Required Vercel environment variables

| Variable | Value |
|---|---|
| `SECRET_KEY` | A unique, randomly generated secret of at least 32 random bytes. Never use the placeholder from `env.example`. |
| `FLASK_ENV` | `production` |
| `FLASK_DEBUG` | `0` |
| `COOKIE_SECURE` | `true` |
| `DB_HOST` | Hostname from your hosted MySQL provider |
| `DB_PORT` | Provider's MySQL port, usually `3306` |
| `DB_USER` | Dedicated application database user |
| `DB_PASSWORD` | Application database password |
| `DB_NAME` | Database name, usually `terrasync` |

Set `GEMINI_API_KEY` to enable live AI triage. `GEMINI_ENABLED=true` is the default; set it to `false` if you intentionally want to deploy without Gemini.

For real OTP delivery, set `SMS_PROVIDER=twilio` and configure `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and either `TWILIO_FROM` or `TWILIO_MESSAGING_SERVICE_SID`. The `console` SMS provider only logs OTPs and is not suitable for public authentication.

## 4. Persistent photo storage (required for photos on Vercel)

Vercel's function filesystem is read-only apart from temporary storage, so local-disk uploads are not durable. Configure an S3-compatible object store and set:

| Variable | Value |
|---|---|
| `S3_BUCKET` | Bucket name |
| `S3_ACCESS_KEY_ID` | Restricted object-storage access key |
| `S3_SECRET_ACCESS_KEY` | Matching secret key |
| `S3_PUBLIC_BASE_URL` | Public base URL that serves uploaded objects, without a trailing slash |
| `S3_REGION` | Provider region; use `auto` for Cloudflare R2 |
| `S3_ENDPOINT_URL` | Optional S3-compatible endpoint; for R2, use the endpoint URL from the R2 dashboard |

The bucket/public URL must allow the browser to fetch uploaded photos. Restrict credentials to the required bucket/prefix. Do not expose storage credentials in HTML or JavaScript.

The upload cap is 4 MB to leave room for multipart request overhead under Vercel's function request-body limit. Photos use random UUID object keys under `uploads/issues/`.

## 5. Deploy and smoke-test

After deployment, check:

- `https://YOUR-DEPLOYMENT/healthz` returns `{"status":"ok","service":"terrasync"}`. This checks Flask only, not database connectivity.
- `/` loads the landing page and the citizen/admin HTML pages load.
- Database-backed login and dashboard endpoints work.
- Citizen OTP works with the configured SMS provider.
- Submit a test issue with and without a photo; confirm the photo URL is publicly readable.
- Test Gemini triage with a real API key and review the Vercel function logs for errors.

## Notes

- The database schema and seed data are not automatically applied during deployment.
- Keep Vercel and MySQL in nearby regions to reduce latency.
- Vercel may require a current CLI for local parity. For local testing, install dependencies and use `vercel dev`.
- Do not treat a successful build or `/healthz` response as proof that the full application works end-to-end.
