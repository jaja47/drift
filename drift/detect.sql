-- Drift detection, run by detect.py in an in-memory DuckDB.
--
-- The input is one table, `pairs`, built by normalize.py: one row per person, with both systems'
-- values already in the same terms. Each mismatch type is one CTE; their union is the result.
--
-- A row says what disagrees, never who the person is: employee_ref is an ID, not a name.
-- The effective-date columns are set only where they explain the mismatch (org date on
-- cost-centre and manager rows, comp date on pay rows), so a shared date means something.
--
-- detect.py loads each `-- name:` section by name.


-- name: schema
-- Mirrors normalize.Pair field for field; tests/test_detect.py checks they agree.
CREATE TABLE pairs (
    employee_ref         VARCHAR NOT NULL,
    match                VARCHAR NOT NULL,
    workday_source       VARCHAR,
    adp_source           VARCHAR,
    associate_id         VARCHAR,
    hire_date            DATE,
    org_effective_date   DATE,
    comp_effective_date  DATE,
    wd_status            VARCHAR,
    adp_status           VARCHAR,
    wd_termination_date  DATE,
    adp_termination_date DATE,
    wd_name              VARCHAR,
    adp_name             VARCHAR,
    wd_name_key          VARCHAR,
    adp_name_key         VARCHAR,
    wd_email             VARCHAR,
    adp_email            VARCHAR,
    wd_cost_center       VARCHAR,
    wd_department        VARCHAR,
    adp_department       VARCHAR,
    wd_location          VARCHAR,
    adp_location         VARCHAR,
    wd_manager           VARCHAR,
    adp_manager          VARCHAR,
    wd_rate_type         VARCHAR,
    adp_rate_type        VARCHAR,
    wd_annual_pay_cents  BIGINT,
    adp_annual_pay_cents BIGINT
);

-- Annual pay in cents → '154000.00/yr', exactly (no floating point).
CREATE TEMP MACRO annual(cents) AS printf('%d.%02d/yr', cents // 100, cents % 100);


-- name: mismatches
-- Parameter: $pay_tolerance_cents (normalize.PAY_TOLERANCE_CENTS — the ±$1 rule, not repeated here)
WITH

-- M1 · terminated in Workday, not terminated in ADP (still active, or on leave, or unrecognised)
m1 AS (
    SELECT 'M1' AS type, employee_ref, 'worker_status' AS field,
           concat_ws(' ', wd_status, CAST(wd_termination_date AS VARCHAR)) AS workday_value,
           concat_ws(' ', adp_status, CAST(adp_termination_date AS VARCHAR)) AS adp_value,
           workday_source, adp_source,
           NULL::DATE AS org_effective_date, NULL::DATE AS comp_effective_date
    FROM pairs
    WHERE match = 'both'
      AND wd_status = 'terminated'
      AND adp_status IS DISTINCT FROM 'terminated'
),

-- M2 · annual pay differs by more than the tolerance, or only one side has a figure
m2 AS (
    SELECT 'M2' AS type, employee_ref, 'pay' AS field,
           annual(wd_annual_pay_cents) AS workday_value,
           annual(adp_annual_pay_cents) AS adp_value,
           workday_source, adp_source,
           NULL::DATE AS org_effective_date, comp_effective_date
    FROM pairs
    WHERE match = 'both'
      AND ((wd_annual_pay_cents IS NULL) <> (adp_annual_pay_cents IS NULL)
           OR abs(wd_annual_pay_cents - adp_annual_pay_cents) > $pay_tolerance_cents)
),

-- M3 · cost centre differs (Workday's, mapped to an ADP department code, vs ADP's)
m3 AS (
    SELECT 'M3' AS type, employee_ref, 'cost_center' AS field,
           wd_cost_center AS workday_value,
           adp_department AS adp_value,
           workday_source, adp_source,
           org_effective_date, NULL::DATE AS comp_effective_date
    FROM pairs
    WHERE match = 'both'
      AND wd_department IS DISTINCT FROM adp_department
),

-- M4 · manager differs (both sides as Workday employee ids)
m4 AS (
    SELECT 'M4' AS type, employee_ref, 'manager' AS field,
           wd_manager AS workday_value,
           adp_manager AS adp_value,
           workday_source, adp_source,
           org_effective_date, NULL::DATE AS comp_effective_date
    FROM pairs
    WHERE match = 'both'
      AND wd_manager IS DISTINCT FROM adp_manager
),

-- M5 · in Workday, missing from ADP. A terminated worker with no payroll record is not a gap.
m5 AS (
    SELECT 'M5' AS type, employee_ref, 'record' AS field,
           concat_ws(' ', wd_status, 'hired', CAST(hire_date AS VARCHAR)) AS workday_value,
           NULL AS adp_value,
           workday_source, adp_source,
           NULL::DATE AS org_effective_date, NULL::DATE AS comp_effective_date
    FROM pairs
    WHERE match = 'workday_only'
      AND wd_status IS DISTINCT FROM 'terminated'
),

-- M6 · in ADP, missing from Workday — whatever its status, a payroll record nobody owns
m6 AS (
    SELECT 'M6' AS type, employee_ref, 'record' AS field,
           NULL AS workday_value,
           concat_ws(' ', adp_status, CAST(adp_termination_date AS VARCHAR)) AS adp_value,
           workday_source, adp_source,
           NULL::DATE AS org_effective_date, NULL::DATE AS comp_effective_date
    FROM pairs
    WHERE match = 'adp_only'
),

-- M7 · legal name differs (compared case- and whitespace-insensitively; preferred name ignored)
m7 AS (
    SELECT 'M7' AS type, employee_ref, 'legal_name' AS field,
           wd_name AS workday_value,
           adp_name AS adp_value,
           workday_source, adp_source,
           NULL::DATE AS org_effective_date, NULL::DATE AS comp_effective_date
    FROM pairs
    WHERE match = 'both'
      AND wd_name_key IS DISTINCT FROM adp_name_key
),

found AS (
    SELECT * FROM m1
    UNION ALL BY NAME SELECT * FROM m2
    UNION ALL BY NAME SELECT * FROM m3
    UNION ALL BY NAME SELECT * FROM m4
    UNION ALL BY NAME SELECT * FROM m5
    UNION ALL BY NAME SELECT * FROM m6
    UNION ALL BY NAME SELECT * FROM m7
)

-- The same disagreement gets the same fingerprint on every run, so a rerun can recognise it.
SELECT sha256(concat_ws('|', type, employee_ref, field,
                        coalesce(workday_value, ''), coalesce(adp_value, ''))) AS fingerprint,
       type, employee_ref, field, workday_value, adp_value,
       workday_source, adp_source, org_effective_date, comp_effective_date
FROM found
ORDER BY type, employee_ref;
