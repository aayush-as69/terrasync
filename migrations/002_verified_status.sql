-- Migration 002: add the 'Verified' issue status.
-- issues.status is VARCHAR(50), so no column change is needed. This only
-- refreshes the KPI views so Verified issues count as closed (included in
-- `resolved` and SLA performance) and are also reported in a `verified`
-- column. Safe to re-run.

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
