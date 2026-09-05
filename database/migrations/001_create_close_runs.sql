CREATE TABLE close_runs (
    close_run_id TEXT PRIMARY KEY,
    journal_source TEXT NOT NULL,
    bank_source TEXT NOT NULL,
    imported_journal_line_count INTEGER NOT NULL,
    imported_bank_transaction_count INTEGER NOT NULL,
    total_exception_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX close_runs_created_at_idx
    ON close_runs(created_at DESC);