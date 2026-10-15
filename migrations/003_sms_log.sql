-- Migration 003: SMS notification log. Safe to re-run.
-- SMS notifications sent (or skipped) to citizens about their complaints.
CREATE TABLE IF NOT EXISTS sms_log (
    log_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    issue_id BIGINT NOT NULL,
    citizen_id BIGINT NULL,
    event VARCHAR(30) NOT NULL,          -- received | assigned | Acknowledged | InProgress | Resolved | Verified
    phone VARCHAR(20) NULL,
    message VARCHAR(500) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',   -- queued | sent | failed | skipped
    error VARCHAR(500) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_sms_issue (issue_id),
    FOREIGN KEY (issue_id) REFERENCES issues(issue_id) ON DELETE CASCADE
);
