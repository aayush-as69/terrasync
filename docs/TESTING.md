# TerraSync Testing

## Automated tests

Run the current offline suite with:

~~~bash
python -m unittest discover -s tests -v
~~~

The trust-layer tests are designed to run without a live database, network or Gemini key.

## What is covered

The AI trust tests exercise schema re-validation, invalid category/department values, hallucinated duplicate identifiers, fallback-model behavior, provider failures, timeout handling, prompt-injection quoting, confidence gating and human-review decisions.

## Manual smoke test

1. Citizen login.
2. Text complaint.
3. Photo and GPS submission.
4. Browser voice input.
5. Gemini success path.
6. Gemini-disabled/fallback path.
7. Low-confidence review path.
8. Duplicate candidate behavior.
9. Staff assignment.
10. Status transitions through Verified.
11. Citizen verification.
12. SMS console provider.
13. Twilio path only when a real test account is configured.

## Test boundary

The current unit suite is strongest around the Gemini trust policy. Expand endpoint/database integration tests before treating TerraSync as production-ready.
