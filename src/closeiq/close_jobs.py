from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from .accounting import load_journal_lines
from .bank_import import import_bank_transactions
from .close_review import build_close_review
from .database import get_connection
from .journal_import import import_journal_entries
from .reconciliation import load_bank_transactions


CLOSE_PERIOD_PATTERN = r"\d{4}-(0[1-9]|1[0-2])"

# Error details are stored in close_jobs and may be surfaced to a reviewer
# later; keep them bounded so a pathological error message can't bloat the
# row indefinitely.
MAX_ERROR_DETAIL_LENGTH = 2000


def create_close_job(
    close_period: str,
    *,
    journal_source: str,
    bank_source: str,
) -> str:
    """Create a durable, queued close job and return its job_id.

    The job_id doubles as the import_batch_id for every row this job's run
    persists (see docs/closeiq_v2_architecture.md, section 3), so the job
    must exist, committed, before ``run_close_job`` imports anything.
    """
    if not re.fullmatch(CLOSE_PERIOD_PATTERN, close_period):
        raise ValueError(
            "close_period must use YYYY-MM format, such as 2026-08"
        )

    job_id = str(uuid4())

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO close_jobs (
                    job_id,
                    status,
                    close_period,
                    journal_source,
                    bank_source
                )
                VALUES (%s, 'queued', %s, %s, %s)
                """,
                (job_id, close_period, journal_source, bank_source),
            )

    return job_id


def get_close_job(job_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    job_id,
                    status,
                    close_period,
                    journal_source,
                    bank_source,
                    close_run_id,
                    error_detail,
                    created_at,
                    updated_at
                FROM close_jobs
                WHERE job_id = %s
                """,
                (job_id,),
            )
            row = cursor.fetchone()

    if row is None:
        return None

    return {
        "job_id": row[0],
        "status": row[1],
        "close_period": row[2],
        "journal_source": row[3],
        "bank_source": row[4],
        "close_run_id": row[5],
        "error_detail": row[6],
        "created_at": row[7],
        "updated_at": row[8],
    }


def _mark_job_running(job_id: str) -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE close_jobs
                SET status = 'running', updated_at = CURRENT_TIMESTAMP
                WHERE job_id = %s
                """,
                (job_id,),
            )


def _mark_job_failed(job_id: str, error_detail: str) -> None:
    # Deliberately opened as its own connection/transaction, separate from
    # the atomic pipeline's connection: that connection has already been
    # rolled back and closed by the time this runs (see run_close_job),
    # and a failure must still be recorded even though the run itself
    # didn't durably change anything.
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE close_jobs
                SET
                    status = 'failed',
                    error_detail = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = %s
                """,
                (error_detail[:MAX_ERROR_DETAIL_LENGTH], job_id),
            )


def _describe_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def run_close_job(
    job_id: str,
    journal_file: str | Path,
    bank_file: str | Path,
) -> dict[str, Any]:
    """Run a queued close job's deterministic pipeline atomically.

    Persists imported journal/bank data, runs the deterministic controls,
    and saves workflow exceptions, the close run, and its exception
    snapshot, all in one database transaction, then marks the job
    ``succeeded`` in that same transaction. If any step raises, the whole
    transaction rolls back (no partial rows survive under this job's
    import_batch_id) and the job is marked ``failed`` in a separate
    transaction with a human-readable error detail.

    See docs/closeiq_v2_architecture.md, section 7 (Phase 1A) for the
    design this implements.
    """
    job = get_close_job(job_id)
    if job is None:
        raise ValueError(f"close job {job_id!r} does not exist")
    if job["status"] != "queued":
        raise ValueError(
            f"close job {job_id!r} is not queued "
            f"(current status: {job['status']!r})"
        )

    close_period = job["close_period"]
    journal_source = job["journal_source"]
    bank_source = job["bank_source"]

    _mark_job_running(job_id)

    try:
        with get_connection() as connection:
            imported_journal_line_count = import_journal_entries(
                journal_file,
                import_batch_id=job_id,
                connection=connection,
            )
            imported_bank_transaction_count = import_bank_transactions(
                bank_file,
                import_batch_id=job_id,
                connection=connection,
            )

            journal_lines = load_journal_lines(journal_file)
            bank_transactions = load_bank_transactions(bank_file)

            close_review = build_close_review(journal_lines, bank_transactions)
            workflow_exceptions = close_review["workflow_exceptions"]

            close_run_id = str(uuid4())

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO close_runs (
                        close_run_id,
                        close_period,
                        journal_source,
                        bank_source,
                        imported_journal_line_count,
                        imported_bank_transaction_count,
                        total_exception_count,
                        import_batch_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        close_run_id,
                        close_period,
                        journal_source,
                        bank_source,
                        imported_journal_line_count,
                        imported_bank_transaction_count,
                        close_review["summary"]["total_exception_count"],
                        job_id,
                    ),
                )

                cursor.executemany(
                    """
                    INSERT INTO close_run_exceptions (
                        close_run_id,
                        exception_id,
                        exception_type,
                        severity,
                        status,
                        source_ids,
                        evidence
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    [
                        (
                            close_run_id,
                            exception["exception_id"],
                            exception["exception_type"],
                            exception["severity"],
                            exception["status"],
                            exception["source_ids"],
                            json.dumps(
                                {
                                    key: value
                                    for key, value in exception.items()
                                    if key
                                    not in {
                                        "exception_id",
                                        "exception_type",
                                        "severity",
                                        "status",
                                        "source_ids",
                                    }
                                },
                                default=str,
                            ),
                        )
                        for exception in workflow_exceptions
                    ],
                )

                cursor.execute(
                    """
                    UPDATE close_jobs
                    SET
                        status = 'succeeded',
                        close_run_id = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = %s
                    """,
                    (close_run_id, job_id),
                )
    except Exception as error:
        _mark_job_failed(job_id, _describe_error(error))
        raise

    return {
        "job_id": job_id,
        "close_run_id": close_run_id,
        "close_period": close_period,
        "journal_source": journal_source,
        "bank_source": bank_source,
        "imported_journal_line_count": imported_journal_line_count,
        "imported_bank_transaction_count": imported_bank_transaction_count,
        "close_review": close_review,
    }
