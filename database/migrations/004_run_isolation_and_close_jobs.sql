-- Phase 1A: durable job/run persistence, plus import-batch scoping for
-- journal and bank data so a later upload can never silently overwrite an
-- earlier close run's source rows.
-- See docs/closeiq_v2_architecture.md, section 3 (Run-level data isolation
-- and idempotency) and section 7 (Phase 1A) for the design this implements.

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

CREATE INDEX close_jobs_status_idx
    ON close_jobs(status);

CREATE INDEX close_jobs_created_at_idx
    ON close_jobs(created_at DESC);

-- Backfill note: this project holds only synthetic demo/test data (see
-- README - "CloseIQ uses synthetic data only"), so pre-migration rows are
-- attributed to a single synthetic placeholder job/batch rather than
-- reconstructed per original run. This placeholder is not a real job and is
-- marked 'failed' with an explanatory error_detail so it can never be
-- mistaken for one. Skipped entirely on a fresh install, since the WHERE
-- clause finds no pre-existing rows.
INSERT INTO close_jobs (
    job_id, status, close_period, journal_source, bank_source, error_detail
)
SELECT
    'legacy-import',
    'failed',
    COALESCE(
        (SELECT close_period FROM close_runs ORDER BY created_at ASC LIMIT 1),
        '2000-01'
    ),
    'legacy',
    'legacy',
    'Synthetic placeholder job created by migration 004 to attribute '
    || 'pre-migration journal/bank/close-run rows to a single legacy '
    || 'import batch. Not a real job run.'
WHERE EXISTS (SELECT 1 FROM journal_entries)
   OR EXISTS (SELECT 1 FROM bank_transactions);

-- journal_entries: scope uniqueness to the import batch instead of a bare
-- global journal_id, so two batches that happen to reuse a journal_id
-- coexist instead of one overwriting the other.
ALTER TABLE journal_entries
    ADD COLUMN import_batch_id TEXT;

UPDATE journal_entries
    SET import_batch_id = 'legacy-import'
    WHERE import_batch_id IS NULL;

ALTER TABLE journal_entries
    ALTER COLUMN import_batch_id SET NOT NULL;

ALTER TABLE journal_entries
    ADD CONSTRAINT journal_entries_import_batch_id_fkey
    FOREIGN KEY (import_batch_id) REFERENCES close_jobs(job_id);

-- journal_lines: carries the same import_batch_id so its reference to
-- journal_entries can be scoped the same way (a bare journal_id is no
-- longer globally unique, so the FK must include the batch id too). This
-- must happen, and the old FK must be dropped, before journal_entries'
-- primary key changes below, since the old FK depends on that key.
ALTER TABLE journal_lines
    ADD COLUMN import_batch_id TEXT;

UPDATE journal_lines
    SET import_batch_id = 'legacy-import'
    WHERE import_batch_id IS NULL;

ALTER TABLE journal_lines
    ALTER COLUMN import_batch_id SET NOT NULL;

ALTER TABLE journal_lines
    DROP CONSTRAINT journal_lines_journal_id_fkey;

ALTER TABLE journal_entries
    DROP CONSTRAINT journal_entries_pkey;

ALTER TABLE journal_entries
    ADD PRIMARY KEY (import_batch_id, journal_id);

CREATE INDEX journal_entries_import_batch_id_idx
    ON journal_entries(import_batch_id);

ALTER TABLE journal_lines
    ADD CONSTRAINT journal_lines_journal_entry_fkey
    FOREIGN KEY (import_batch_id, journal_id)
    REFERENCES journal_entries(import_batch_id, journal_id);

ALTER TABLE journal_lines
    DROP CONSTRAINT journal_line_unique_position;

ALTER TABLE journal_lines
    ADD CONSTRAINT journal_line_unique_position
    UNIQUE (import_batch_id, journal_id, line_number);

CREATE INDEX journal_lines_import_batch_id_idx
    ON journal_lines(import_batch_id);

-- bank_transactions: scope uniqueness to the import batch, same rationale
-- as journal_entries above.
ALTER TABLE bank_transactions
    ADD COLUMN import_batch_id TEXT;

UPDATE bank_transactions
    SET import_batch_id = 'legacy-import'
    WHERE import_batch_id IS NULL;

ALTER TABLE bank_transactions
    ALTER COLUMN import_batch_id SET NOT NULL;

ALTER TABLE bank_transactions
    ADD CONSTRAINT bank_transactions_import_batch_id_fkey
    FOREIGN KEY (import_batch_id) REFERENCES close_jobs(job_id);

ALTER TABLE bank_transactions
    DROP CONSTRAINT bank_transactions_pkey;

ALTER TABLE bank_transactions
    ADD PRIMARY KEY (import_batch_id, bank_transaction_id);

CREATE INDEX bank_transactions_import_batch_id_idx
    ON bank_transactions(import_batch_id);

-- close_runs: record which import batch/job produced this run, so a
-- close run's own source rows can always be found later (used by the
-- future run-scoped AI evidence tools; see the architecture doc).
-- Not declared UNIQUE: the legacy backfill above intentionally maps every
-- pre-migration close_runs row to the same 'legacy-import' placeholder
-- batch, which would violate a one-batch-per-run uniqueness constraint.
-- Every run created going forward is produced by exactly one job/batch by
-- construction (see close_jobs.py), so this is enforced by application
-- code, not the schema, for now.
ALTER TABLE close_runs
    ADD COLUMN import_batch_id TEXT;

UPDATE close_runs
    SET import_batch_id = 'legacy-import'
    WHERE import_batch_id IS NULL;

ALTER TABLE close_runs
    ALTER COLUMN import_batch_id SET NOT NULL;

ALTER TABLE close_runs
    ADD CONSTRAINT close_runs_import_batch_id_fkey
    FOREIGN KEY (import_batch_id) REFERENCES close_jobs(job_id);

CREATE INDEX close_runs_import_batch_id_idx
    ON close_runs(import_batch_id);
