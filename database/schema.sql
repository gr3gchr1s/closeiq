CREATE TABLE accounts (
    account_code VARCHAR(10) PRIMARY KEY,
    account_name TEXT NOT NULL,
    account_type TEXT NOT NULL CHECK (
        account_type IN ('asset', 'liability', 'equity', 'revenue', 'expense')
    ),
    normal_balance TEXT NOT NULL CHECK (
        normal_balance IN ('debit', 'credit')
    )
);

CREATE TABLE close_runs (
    close_run_id TEXT PRIMARY KEY,
    close_period TEXT NOT NULL CHECK (
        close_period ~ '^\d{4}-(0[1-9]|1[0-2])$'
    ),
    journal_source TEXT NOT NULL,
    bank_source TEXT NOT NULL,
    imported_journal_line_count INTEGER NOT NULL,
    imported_bank_transaction_count INTEGER NOT NULL,
    total_exception_count INTEGER NOT NULL,
    -- import_batch_id (added below, after close_jobs exists) records which
    -- job/import batch produced this run. Not declared UNIQUE: every run
    -- created going forward is produced by exactly one job/batch by
    -- construction (see close_jobs.py), enforced by application code
    -- rather than the schema, so a future backfill of unrelated historical
    -- data is never blocked by a uniqueness constraint here.
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- close_jobs is the durable record of one upload's lifecycle
-- (queued -> running -> succeeded/failed). import_batch_id on the tables
-- below is always a close_jobs.job_id: the job ID doubles as the import
-- batch ID, so every imported row is traceable to the job that created it.
-- See docs/closeiq_v2_architecture.md, sections 2 and 3.
CREATE TABLE close_jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed')
    ),
    close_period TEXT NOT NULL CHECK (
        close_period ~ '^\d{4}-(0[1-9]|1[0-2])$'
    ),
    journal_source TEXT NOT NULL,
    bank_source TEXT NOT NULL,
    close_run_id TEXT REFERENCES close_runs(close_run_id),
    error_detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE close_runs
    ADD COLUMN import_batch_id TEXT NOT NULL
        REFERENCES close_jobs(job_id);

-- Every journal/bank row belongs to the import batch that created it, and
-- uniqueness is scoped to the batch rather than global, so two batches
-- that happen to reuse the same journal_id/bank_transaction_id coexist
-- instead of one overwriting the other. See
-- docs/closeiq_v2_architecture.md, section 3 (Run-level data isolation
-- and idempotency).

CREATE TABLE journal_entries (
    import_batch_id TEXT NOT NULL REFERENCES close_jobs(job_id),
    journal_id TEXT NOT NULL,
    journal_date DATE NOT NULL,
    description TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (import_batch_id, journal_id)
);

CREATE TABLE journal_lines (
    journal_line_id BIGSERIAL PRIMARY KEY,
    import_batch_id TEXT NOT NULL,
    journal_id TEXT NOT NULL,
    line_number INTEGER NOT NULL,
    account_code VARCHAR(10) NOT NULL REFERENCES accounts(account_code),
    description TEXT NOT NULL,
    debit NUMERIC(14, 2) NOT NULL DEFAULT 0,
    credit NUMERIC(14, 2) NOT NULL DEFAULT 0,
    external_reference TEXT,
    CONSTRAINT journal_line_amount_check CHECK (
        (debit > 0 AND credit = 0)
        OR (credit > 0 AND debit = 0)
    ),
    CONSTRAINT journal_lines_journal_entry_fkey
        FOREIGN KEY (import_batch_id, journal_id)
        REFERENCES journal_entries(import_batch_id, journal_id),
    CONSTRAINT journal_line_unique_position
        UNIQUE (import_batch_id, journal_id, line_number)
);

CREATE TABLE bank_transactions (
    import_batch_id TEXT NOT NULL REFERENCES close_jobs(job_id),
    bank_transaction_id TEXT NOT NULL,
    transaction_date DATE NOT NULL,
    description TEXT NOT NULL,
    amount NUMERIC(14, 2) NOT NULL,
    external_reference TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (import_batch_id, bank_transaction_id)
);

-- close_run_exceptions is the one reviewable-exception record: it is
-- scoped to the close run that detected it (PRIMARY KEY includes
-- close_run_id), so two runs that detect the same logical exception
-- (same exception_id "fingerprint", e.g. because they share source data)
-- each get their own independently reviewable row. exception_id is a
-- stable fingerprint for comparing across runs (see close_review.py); it
-- is deliberately not globally unique on its own, since the same
-- fingerprint can and does recur across runs. There is no separate
-- global "close_exceptions" table: a decision changes status here,
-- directly, for exactly the run it was made against.
-- See docs/closeiq_v2_architecture.md, section 3.
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

CREATE TABLE exception_decisions (
    decision_id BIGSERIAL PRIMARY KEY,
    close_run_id TEXT NOT NULL,
    exception_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (
        decision IN ('acknowledge', 'resolve', 'dismiss')
    ),
    reviewer TEXT NOT NULL,
    note TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT exception_decisions_close_run_exception_fkey
        FOREIGN KEY (close_run_id, exception_id)
        REFERENCES close_run_exceptions(close_run_id, exception_id)
);

CREATE INDEX close_runs_created_at_idx
    ON close_runs(created_at DESC);

CREATE INDEX close_runs_close_period_created_at_idx
    ON close_runs(close_period, created_at DESC);

CREATE INDEX close_runs_import_batch_id_idx
    ON close_runs(import_batch_id);

CREATE INDEX close_jobs_status_idx
    ON close_jobs(status);

CREATE INDEX close_jobs_created_at_idx
    ON close_jobs(created_at DESC);

CREATE INDEX journal_entries_import_batch_id_idx
    ON journal_entries(import_batch_id);

CREATE INDEX journal_lines_import_batch_id_idx
    ON journal_lines(import_batch_id);

CREATE INDEX bank_transactions_import_batch_id_idx
    ON bank_transactions(import_batch_id);

CREATE INDEX close_run_exceptions_close_run_id_idx
    ON close_run_exceptions(close_run_id);

CREATE INDEX close_run_exceptions_status_severity_idx
    ON close_run_exceptions(status, severity);

CREATE INDEX exception_decisions_close_run_exception_idx
    ON exception_decisions(close_run_id, exception_id);

CREATE INDEX journal_lines_external_reference_idx
    ON journal_lines(external_reference);

CREATE INDEX bank_transactions_external_reference_idx
    ON bank_transactions(external_reference);
