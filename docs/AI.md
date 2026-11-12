# TerraSync AI

## Purpose

Gemini classifies and enriches citizen reports. It is an advisory layer and has no direct database write capability.

## Request flow

~~~text
Citizen description + photo + GPS
          |
          v
     Gemini request
          |
          v
Structured response
          |
          v
Schema + database-value validation
          |
          v
Confidence / civic / duplicate gates
       /        \
      v          v
Safe routing   Human review
~~~

## Trust controls

- Expected fields are constrained to the application's schema.
- Categories and departments are checked against live database values.
- Duplicate identifiers must belong to the candidate set.
- Confidence is clamped to the expected range.
- Low-confidence and non-civic reports retain the safer human-review path.
- Citizen text is treated as data rather than model instructions.
- A primary Gemini model can fall back to a configured secondary model.
- If both fail, the local deterministic fallback keeps the complaint workflow alive.
- A per-citizen hourly AI limit provides a cost guard.

## Automatic assignment

Automatic assignment is allowed only when the configured policy permits it and the AI result is sufficiently confident and non-duplicate. Otherwise the issue remains in the department queue.

## Failure behavior

An AI outage must not delete or reject the complaint. The issue can be filed with a fallback result or marked for review.

## Testing

The existing trust-layer tests cover validation, fallback behavior, timeouts, prompt-injection quoting and confidence/review policy. Live provider quality still requires a configured API key and representative manual testing.
