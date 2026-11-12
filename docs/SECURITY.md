# TerraSync Security

## Security boundary

The browser is an untrusted client. Flask owns authentication, authorization, workflow transitions, AI orchestration and database writes.

## Authentication

Citizen access supports phone OTP and email/password. Staff access uses employee code, password and OTP. Sessions are server-side and revocable.

## AI security

Gemini is never called directly from browser code. The API key remains server-side.

Model output is treated as untrusted input and validated before it can affect routing. Prompt-injection defenses reduce the chance that citizen text is interpreted as instructions.

## Secrets

Keep SECRET_KEY, GEMINI_API_KEY, database credentials and Twilio credentials only in server environment configuration. Never commit .env or expose secrets in frontend JavaScript.

## Demo mode

Development OTP shortcuts and seeded credentials are demonstration mechanisms, not production authentication. Disable development shortcuts before deployment.

## Database

Use a dedicated application database user with only the permissions required by TerraSync. Do not run the production application with MySQL root credentials.

## Uploads

Citizen photos are currently handled by the application filesystem. For production, use controlled object storage, content-type/size validation and appropriate access controls.

## Production controls still required

- HTTPS
- Rate limiting on login and OTP endpoints
- Secure production cookies
- Database backups and restore tests
- Centralized monitoring/logging
- Object storage for uploads
- Secret rotation
- Dependency and container scanning

This document describes implemented boundaries and recommended deployment controls. It is not a claim of independent penetration testing or production security certification.
