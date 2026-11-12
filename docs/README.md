# TerraSync Documentation

Technical documentation for TerraSync, the Gemini-assisted civic complaint and resolution prototype.

## Documentation map

| Document | Purpose |
|---|---|
| ARCHITECTURE.md | Request, trust-layer and workflow architecture |
| SETUP.md | Local, Docker and environment setup |
| AI.md | Gemini validation, confidence gating and fallback behavior |
| SECURITY.md | Authentication, secrets, upload and authorization boundaries |
| API.md | Endpoint inventory and access rules |
| TESTING.md | Unit, integration and pre-demo verification |
| DEPLOYMENT.md | Docker, Gunicorn, Caddy and production checklist |
| OPERATIONS.md | Database, migrations, SMS and monitoring procedures |

## Core design principle

Gemini is an advisory analysis layer. It never receives a database write capability. Flask validates the model response, applies confidence and duplicate gates, and decides whether the ticket can be auto-routed or must go to a human queue.

## Source-of-truth rule

The implementation and tests are authoritative. Documentation must not claim live Twilio, cloud deployment, Hinglish recognition, distributed agents or production authentication unless those paths have been verified in the current environment.