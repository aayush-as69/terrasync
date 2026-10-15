CREATE DATABASE IF NOT EXISTS terrasync;
USE terrasync;

-- Citizens Table
CREATE TABLE IF NOT EXISTS citizens (
    citizen_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    display_name VARCHAR(255) NOT NULL,
    phone VARCHAR(20) UNIQUE,
    email VARCHAR(255) UNIQUE,
    password_hash VARCHAR(255),
    is_verified BOOLEAN DEFAULT FALSE,
    home_ward_id INT,
    points INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Citizen Notification Preferences
CREATE TABLE IF NOT EXISTS citizen_notification_prefs (
    citizen_id BIGINT PRIMARY KEY,
    notify_status_changes BOOLEAN DEFAULT TRUE,
    notify_officer_replies BOOLEAN DEFAULT TRUE,
    notify_nearby_incidents BOOLEAN DEFAULT FALSE,
    notify_ward_announcements BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (citizen_id) REFERENCES citizens(citizen_id) ON DELETE CASCADE
);

-- Departments Table
CREATE TABLE IF NOT EXISTS departments (
    dept_id INT AUTO_INCREMENT PRIMARY KEY,
    dept_name VARCHAR(100) NOT NULL
);

-- Staff Table
CREATE TABLE IF NOT EXISTS staff (
    staff_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    employee_code VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL,
    phone VARCHAR(20),
    dept_id INT,
    ward_id INT,
    duty_status ENUM('available', 'onsite', 'offduty') DEFAULT 'available',
    is_active BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (dept_id) REFERENCES departments(dept_id) ON DELETE SET NULL
    -- staff.ward_id -> wards(ward_id) FK added below via ALTER TABLE, since
    -- the wards table isn't defined until later in this file.
);

-- Sessions Table (Matches the UUID session handling in app.py)
CREATE TABLE IF NOT EXISTS sessions (
    session_id VARCHAR(255) PRIMARY KEY,
    actor_type ENUM('citizen', 'staff') NOT NULL,
    citizen_id BIGINT NULL,
    staff_id BIGINT NULL,
    ip_address VARCHAR(45),
    user_agent TEXT,
    expires_at DATETIME NOT NULL,
    FOREIGN KEY (citizen_id) REFERENCES citizens(citizen_id) ON DELETE CASCADE,
    FOREIGN KEY (staff_id) REFERENCES staff(staff_id) ON DELETE CASCADE
);

-- OTP Tables
-- otp_hash stores an HMAC-SHA256 of the code (keyed with SECRET_KEY), never
-- the raw digits, so a DB dump alone can't be used to log in. attempts +
-- created_at back the max-verification-attempts, resend-cooldown, and
-- per-phone/per-IP rate limiting in app.py; ip_address backs the per-IP
-- limit specifically.
CREATE TABLE IF NOT EXISTS citizen_otp_codes (
    otp_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    phone VARCHAR(20) NOT NULL,
    otp_hash CHAR(64) NOT NULL,
    attempts INT NOT NULL DEFAULT 0,
    ip_address VARCHAR(45) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    consumed_at DATETIME NULL,
    INDEX idx_citizen_otp_phone (phone, otp_id)
);

CREATE TABLE IF NOT EXISTS staff_otp_codes (
    otp_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    staff_id BIGINT NOT NULL,
    otp_hash CHAR(64) NOT NULL,
    attempts INT NOT NULL DEFAULT 0,
    ip_address VARCHAR(45) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    consumed_at DATETIME NULL,
    FOREIGN KEY (staff_id) REFERENCES staff(staff_id) ON DELETE CASCADE,
    INDEX idx_staff_otp_staff (staff_id, otp_id)
);

-- Wards Table
CREATE TABLE IF NOT EXISTS wards (
    ward_id INT AUTO_INCREMENT PRIMARY KEY,
    ward_name VARCHAR(50) NOT NULL,
    sla_hours INT DEFAULT 48
);

-- staff.ward_id conceptually references wards (see the LEFT JOIN in
-- /api/staff/officers) but wasn't enforced at the DB level. Add the FK now
-- that wards exists. Guarded with a procedure so re-running schema.sql
-- doesn't error if the constraint is already there.
DELIMITER //
CREATE PROCEDURE terrasync_add_staff_ward_fk()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA = DATABASE()
          AND TABLE_NAME = 'staff'
          AND CONSTRAINT_NAME = 'fk_staff_ward_id'
    ) THEN
        ALTER TABLE staff
            ADD CONSTRAINT fk_staff_ward_id
            FOREIGN KEY (ward_id) REFERENCES wards(ward_id) ON DELETE SET NULL;
    END IF;
END //
DELIMITER ;
CALL terrasync_add_staff_ward_fk();
DROP PROCEDURE terrasync_add_staff_ward_fk;

-- Issue Categories Table
CREATE TABLE IF NOT EXISTS issue_categories (
    category_id VARCHAR(50) PRIMARY KEY,
    category_name VARCHAR(100) NOT NULL
);

-- Issues Table
CREATE TABLE IF NOT EXISTS issues (
    issue_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    issue_code VARCHAR(50) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    category_id VARCHAR(50),
    ward_id INT,
    severity VARCHAR(20) DEFAULT 'med',
    status VARCHAR(50) DEFAULT 'Reported',
    assigned_staff_id BIGINT NULL,
    citizen_id BIGINT NOT NULL,
    latitude DECIMAL(10, 8),
    longitude DECIMAL(11, 8),
    location_text VARCHAR(255),
    photo_url VARCHAR(500),
    -- Gemini civic intelligence fields
    ai_category_id VARCHAR(50),
    ai_severity VARCHAR(20),
    ai_summary TEXT,
    ai_department VARCHAR(100),
    ai_risk VARCHAR(64),
    ai_detected_language VARCHAR(80),
    ai_duplicate_signal VARCHAR(20),
    ai_similar_issue_code VARCHAR(50),
    ai_duplicate_reason TEXT,
    ai_confidence DECIMAL(5,4) DEFAULT 0,
    ai_model VARCHAR(100),
    ai_processed_at DATETIME NULL,
    ai_status VARCHAR(10) NULL,                  -- done | failed | skipped (NULL = never analysed)
    ai_needs_review BOOLEAN NOT NULL DEFAULT FALSE,  -- low confidence / non-civic / AI unavailable: a human should check
    ai_duplicate_confidence DECIMAL(3,2) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_issues_ai_guard (citizen_id, ai_status, ai_processed_at),
    FOREIGN KEY (citizen_id) REFERENCES citizens(citizen_id),
    FOREIGN KEY (assigned_staff_id) REFERENCES staff(staff_id) ON DELETE SET NULL,
    FOREIGN KEY (category_id) REFERENCES issue_categories(category_id) ON DELETE SET NULL,
    FOREIGN KEY (ai_category_id) REFERENCES issue_categories(category_id) ON DELETE SET NULL
);

-- Issue Status History
CREATE TABLE IF NOT EXISTS issue_status_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    issue_id BIGINT NOT NULL,
    status VARCHAR(50) NOT NULL,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (issue_id) REFERENCES issues(issue_id) ON DELETE CASCADE
);

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

-- Helper view: the most recent time each issue entered 'Resolved' (or
-- 'Verified', for rows with no Resolved history), taken
-- from issue_status_history (there is no issues.updated_at column).
CREATE OR REPLACE VIEW v_issue_resolved_at AS
SELECT issue_id, MAX(changed_at) AS resolved_at
FROM issue_status_history
WHERE status IN ('Resolved', 'Verified')
GROUP BY issue_id;

-- Ward-level KPIs for the Admin dashboard: issue counts by stage plus
-- SLA performance (% of resolved issues closed within the ward's sla_hours).
CREATE OR REPLACE VIEW v_ward_stats AS
SELECT
    w.ward_id,
    w.ward_name,
    w.sla_hours,
    COUNT(i.issue_id) AS total_issues,
    SUM(CASE WHEN i.status = 'Reported' THEN 1 ELSE 0 END) AS reported,
    SUM(CASE WHEN i.status = 'Acknowledged' THEN 1 ELSE 0 END) AS acknowledged,
    SUM(CASE WHEN i.status = 'InProgress' THEN 1 ELSE 0 END) AS in_progress,
    SUM(CASE WHEN i.status IN ('Resolved', 'Verified') THEN 1 ELSE 0 END) AS resolved,
    SUM(CASE WHEN i.status = 'Verified' THEN 1 ELSE 0 END) AS verified,
    ROUND(
        100 * SUM(CASE WHEN i.status IN ('Resolved', 'Verified') AND r.resolved_at IS NOT NULL
                             AND TIMESTAMPDIFF(HOUR, i.created_at, r.resolved_at) <= w.sla_hours
                        THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN i.status IN ('Resolved', 'Verified') THEN 1 ELSE 0 END), 0),
    1) AS sla_performance_pct
FROM wards w
LEFT JOIN issues i ON i.ward_id = w.ward_id
LEFT JOIN v_issue_resolved_at r ON r.issue_id = i.issue_id
GROUP BY w.ward_id, w.ward_name, w.sla_hours;

-- Department-level KPIs, rolled up via each issue's assigned officer's
-- department (issues has no dept_id of its own).
CREATE OR REPLACE VIEW v_department_stats AS
SELECT
    d.dept_id,
    d.dept_name,
    COUNT(i.issue_id) AS total_issues,
    SUM(CASE WHEN i.status = 'Reported' THEN 1 ELSE 0 END) AS reported,
    SUM(CASE WHEN i.status = 'Acknowledged' THEN 1 ELSE 0 END) AS acknowledged,
    SUM(CASE WHEN i.status = 'InProgress' THEN 1 ELSE 0 END) AS in_progress,
    SUM(CASE WHEN i.status IN ('Resolved', 'Verified') THEN 1 ELSE 0 END) AS resolved,
    SUM(CASE WHEN i.status = 'Verified' THEN 1 ELSE 0 END) AS verified
FROM departments d
LEFT JOIN staff s ON s.dept_id = d.dept_id
LEFT JOIN issues i ON i.assigned_staff_id = s.staff_id
GROUP BY d.dept_id, d.dept_name;
