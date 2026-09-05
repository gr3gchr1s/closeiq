ALTER TABLE close_runs
    ADD COLUMN close_period TEXT;

UPDATE close_runs
SET close_period = TO_CHAR(
    created_at AT TIME ZONE 'UTC',
    'YYYY-MM'
)
WHERE close_period IS NULL;

ALTER TABLE close_runs
    ALTER COLUMN close_period SET NOT NULL;

ALTER TABLE close_runs
    ADD CONSTRAINT close_runs_close_period_format_check
    CHECK (close_period ~ '^\d{4}-(0[1-9]|1[0-2])$');

CREATE INDEX close_runs_close_period_created_at_idx
    ON close_runs(close_period, created_at DESC);