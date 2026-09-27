-- Make workflow exceptions and reviewer decisions run-scoped.
--
-- Before this migration, "close_exceptions" was a single global, mutable
-- row per exception_id "fingerprint" (e.g. journal-balance:JE-1004), and
-- "exception_decisions" referenced that global exception_id directly.
-- Since source data and close runs are now import-batch/run scoped (see
-- migration 004), two different runs that happen to detect the same
-- fingerprint (e.g. because they share source data) shared one row in
-- close_exceptions and could have their status/decisions leak into each
-- other. "close_run_exceptions" already had the right shape for this
-- (PRIMARY KEY (close_run_id, exception_id)), but was write-once and
-- never updated by a decision.
--
-- After this migration, close_run_exceptions is the one reviewable,
-- mutable exception record (status changes belong to it directly), and
-- exception_decisions references it via the composite
-- (close_run_id, exception_id) key. close_exceptions is dropped.
-- See docs/closeiq_v2_architecture.md, section 3, and
-- database/schema.sql for the resulting fresh-install shape.

-- 1. Add close_run_id to exception_decisions (nullable for now, so
--    existing rows can be backfilled below).
ALTER TABLE exception_decisions
    ADD COLUMN close_run_id TEXT;

-- 2. Backfill: attribute each existing decision to the most recently
--    created close_run_exceptions row sharing its exception_id. This is a
--    best-effort heuristic for pre-migration data (this project holds
--    only synthetic demo/test data - see README), since the prior schema
--    had no way to record which run a decision was actually made against.
UPDATE exception_decisions ed
SET close_run_id = (
    SELECT cre.close_run_id
    FROM close_run_exceptions cre
    WHERE cre.exception_id = ed.exception_id
    ORDER BY cre.created_at DESC, cre.close_run_id DESC
    LIMIT 1
)
WHERE ed.close_run_id IS NULL;

-- 3. A decision whose exception_id matches no close_run_exceptions row at
--    all (e.g. a decision recorded only against the old global
--    close_exceptions table, with no corresponding run snapshot) cannot
--    be attributed to any run. Such a decision cannot be made run-scoped
--    correctly and is removed rather than kept with a fabricated
--    attribution; on this project's synthetic data this is expected to
--    affect zero rows in normal use, since every real decision is made
--    against an exception surfaced from some run's snapshot.
DELETE FROM exception_decisions
WHERE close_run_id IS NULL;

ALTER TABLE exception_decisions
    ALTER COLUMN close_run_id SET NOT NULL;

-- 4. Best-effort backfill of close_run_exceptions.status from the old
--    global close_exceptions table: for each exception_id, the most
--    recently created close_run_exceptions row is treated as "the run a
--    decision was probably about" and takes on that exception's last
--    known global status. Older runs sharing the same fingerprint keep
--    their originally recorded status (usually 'open'), since there is no
--    way to know a decision was ever made against them specifically -
--    this is exactly the ambiguity this migration removes going forward.
UPDATE close_run_exceptions cre
SET status = ce.status
FROM close_exceptions ce
WHERE cre.exception_id = ce.exception_id
  AND cre.close_run_id = (
      SELECT cre2.close_run_id
      FROM close_run_exceptions cre2
      WHERE cre2.exception_id = ce.exception_id
      ORDER BY cre2.created_at DESC, cre2.close_run_id DESC
      LIMIT 1
  );

-- 5. Drop the old FK and replace it with the composite, run-scoped one.
ALTER TABLE exception_decisions
    DROP CONSTRAINT exception_decisions_exception_id_fkey;

ALTER TABLE exception_decisions
    ADD CONSTRAINT exception_decisions_close_run_exception_fkey
    FOREIGN KEY (close_run_id, exception_id)
    REFERENCES close_run_exceptions(close_run_id, exception_id);

CREATE INDEX exception_decisions_close_run_exception_idx
    ON exception_decisions(close_run_id, exception_id);

-- 6. close_exceptions is superseded entirely; drop it and its index.
DROP INDEX IF EXISTS close_exceptions_status_severity_idx;
DROP TABLE close_exceptions;

-- 7. close_run_exceptions is now a live, reviewable table rather than a
--    write-once snapshot, so it needs the same status/severity lookup
--    index close_exceptions used to have.
CREATE INDEX close_run_exceptions_status_severity_idx
    ON close_run_exceptions(status, severity);
