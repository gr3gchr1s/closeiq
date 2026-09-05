CREATE TABLE close_run_exceptions (
    close_run_id TEXT NOT NULL REFERENCES close_runs(close_run_id),
    exception_id TEXT NOT NULL,
    exception_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (
        severity IN ('low', 'medium', 'high')
    ),
    status TEXT NOT NULL CHECK (
        status IN ('open', 'reviewed', 'resolved', 'dismissed')
    ),
    source_ids TEXT[] NOT NULL,
    evidence JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (close_run_id, exception_id)
);

CREATE INDEX close_run_exceptions_close_run_id_idx
    ON close_run_exceptions(close_run_id);