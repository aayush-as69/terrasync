USE terrasync;

-- Migration 004: Gemini trust-policy support.
--   ai_status               done | failed | skipped. Also drives the per-citizen hourly AI cost guard.
--   ai_needs_review         TRUE when AI was unavailable, not confident, or the report wasn't a civic issue.
--   ai_duplicate_confidence Gemini's confidence in its duplicate match.
-- Run once against an existing database (after 001-003).
ALTER TABLE issues
  ADD COLUMN ai_status VARCHAR(10) NULL,
  ADD COLUMN ai_needs_review BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN ai_duplicate_confidence DECIMAL(3,2) NULL;

-- Cost-guard lookups: "this citizen's successful AI analyses in the last hour".
CREATE INDEX idx_issues_ai_guard ON issues (citizen_id, ai_status, ai_processed_at);
