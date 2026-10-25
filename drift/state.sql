-- Drift's own state, state/drift.db (SQLite): what governance did with each mismatch, and each run.
--
-- It holds ids, classes and outcomes only — no names, emails or pay. The values themselves are
-- recomputed from the exports whenever they are needed. Warrant's ledger is the record of what was
-- authorised; this file is Drift's working copy of where each mismatch stands.
--
-- state.py loads each `-- name:` section by name.


-- name: schema
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    principal   TEXT NOT NULL,
    outcome     TEXT,  -- completed | warrant_unreachable
    detail      TEXT,
    report      TEXT   -- path of reports/run_<ts>.md
);

CREATE TABLE IF NOT EXISTS mismatches (
    fingerprint  TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    employee_ref TEXT NOT NULL,
    field        TEXT NOT NULL,
    change_class TEXT NOT NULL,
    action       TEXT NOT NULL,  -- update | create | deactivate
    status       TEXT NOT NULL,  -- see state.STATUSES
    detail       TEXT,
    warrant_id   TEXT,
    approval_id  TEXT,
    requested_of TEXT,
    decided_by   TEXT,
    violations   TEXT,  -- JSON: Warrant's 422 violations
    result       TEXT,  -- JSON: what was written, as committed to Warrant
    first_run    TEXT NOT NULL,
    last_run     TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);


-- name: save_mismatch
INSERT INTO mismatches (fingerprint, type, employee_ref, field, change_class, action, status,
                        detail, warrant_id, approval_id, requested_of, decided_by, violations,
                        result, first_run, last_run, updated_at)
VALUES (:fingerprint, :type, :employee_ref, :field, :change_class, :action, :status,
        :detail, :warrant_id, :approval_id, :requested_of, :decided_by, :violations,
        :result, :first_run, :last_run, :updated_at)
ON CONFLICT (fingerprint) DO UPDATE SET
    type = excluded.type,
    employee_ref = excluded.employee_ref,
    field = excluded.field,
    change_class = excluded.change_class,
    action = excluded.action,
    status = excluded.status,
    detail = excluded.detail,
    warrant_id = excluded.warrant_id,
    approval_id = excluded.approval_id,
    requested_of = excluded.requested_of,
    decided_by = excluded.decided_by,
    violations = excluded.violations,
    result = excluded.result,
    last_run = excluded.last_run,
    updated_at = excluded.updated_at;


-- name: get_mismatch
SELECT * FROM mismatches WHERE fingerprint = :fingerprint;


-- name: all_mismatches
SELECT * FROM mismatches ORDER BY type, employee_ref, field;


-- name: start_run
INSERT INTO runs (run_id, started_at, principal) VALUES (:run_id, :started_at, :principal);


-- name: finish_run
UPDATE runs SET finished_at = :finished_at, outcome = :outcome, detail = :detail, report = :report
WHERE run_id = :run_id;


-- name: all_runs
SELECT * FROM runs ORDER BY started_at;
