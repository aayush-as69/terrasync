# TerraSync API

## API boundary

The browser communicates with Flask over HTTP. Authentication, authorization and workflow validation happen server-side.

## Main areas

| Area | Examples |
|---|---|
| Citizen auth | OTP send/verify, email registration/login, session |
| Citizen issues | Create/list/view reports, verify resolved issue |
| Staff auth | Employee login, OTP verification, session |
| Staff issues | Queue, assignment, status transitions, review |
| Analytics | Ward/department KPIs and SLA views |
| SMS | Notification log and provider status |

## Workflow

~~~text
Reported → Acknowledged → InProgress → Resolved → Verified
~~~

Verified is the final state and can be reached after a resolved issue is confirmed.

## Authentication

Protected endpoints require the appropriate server session. Staff endpoints additionally enforce staff roles.

Development-only shortcuts must not be interpreted as production authentication.

## API discovery

For exact request/response shapes, inspect the route definitions in app.py and the frontend API calls. This guide intentionally avoids inventing endpoint contracts that can drift from the implementation.
