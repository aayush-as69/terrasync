USE terrasync;

-- Run once against an existing TerraSync database.
ALTER TABLE issues
  ADD COLUMN ai_category_id VARCHAR(50) NULL,
  ADD COLUMN ai_severity VARCHAR(20) NULL,
  ADD COLUMN ai_summary TEXT NULL,
  ADD COLUMN ai_department VARCHAR(100) NULL,
  ADD COLUMN ai_risk VARCHAR(64) NULL,
  ADD COLUMN ai_detected_language VARCHAR(80) NULL,
  ADD COLUMN ai_duplicate_signal VARCHAR(20) NULL,
  ADD COLUMN ai_similar_issue_code VARCHAR(50) NULL,
  ADD COLUMN ai_duplicate_reason TEXT NULL,
  ADD COLUMN ai_confidence DECIMAL(5,4) DEFAULT 0,
  ADD COLUMN ai_model VARCHAR(100) NULL,
  ADD COLUMN ai_processed_at DATETIME NULL;

ALTER TABLE issues
  ADD CONSTRAINT fk_issues_ai_category
  FOREIGN KEY (ai_category_id) REFERENCES issue_categories(category_id) ON DELETE SET NULL;
