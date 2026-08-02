-- Production diagnosis history is stored only in aiops.diagnosis_report.
-- Run once against the Docker smartlife-mysql instance before deploying the new code.
-- Existing rows remain intact and have NULL session_id until separately backfilled.

USE aiops;

ALTER TABLE diagnosis_report
    ADD COLUMN session_id VARCHAR(255) NULL AFTER alert_id;

CREATE INDEX idx_report_session ON diagnosis_report(session_id);
