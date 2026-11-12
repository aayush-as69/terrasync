# TerraSync Deployment

## Included deployment assets

The repository includes Docker/Gunicorn configuration and an optional Caddy reverse-proxy configuration.

## Container deployment

~~~bash
docker compose up -d --build
~~~

Use a production .env and a scoped database user.

## Reverse proxy

When Caddy fronts the application, bind the application privately so the plain HTTP port is not exposed directly. Use HTTPS for the public site, especially because browser microphone access normally requires a secure context.

## Production configuration

Set FLASK_ENV=production, a strong SECRET_KEY, COOKIE_SECURE=true, production database credentials and valid Gemini/SMS settings when those integrations are enabled. Disable development OTP shortcuts.

## Operational requirements

Before a public deployment:

- Add login/OTP rate limiting
- Use object storage for issue photos
- Configure database backups
- Monitor AI failure/review rates
- Rotate secrets
- Verify SMS delivery and India sender/template requirements
- Test restore procedures
- Run the application behind HTTPS

The deployment files do not by themselves prove that TerraSync has been deployed to or validated on a cloud provider.
