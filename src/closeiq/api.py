import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .close_jobs import create_close_job, get_close_job, run_close_job
from .database import get_connection
from .period_inference import (
    EmptyFileError,
    MissingDateColumnError,
    PeriodAmbiguousError,
    UnparseableDatesError,
    infer_close_period,
)


app = FastAPI(
    title="CloseIQ API",
    version="0.1.0",
)

logger = logging.getLogger(__name__)

# Fixed, server-chosen upload filenames — never the client-supplied
# UploadFile.filename, which is recorded only as display data (see
# docs/phase_1b_spec.md, section 3.4).
JOURNAL_FILENAME = "journal.csv"
BANK_FILENAME = "bank.csv"

# Fixed, application-controlled path to the static frontend (Workstream
# C). Never derived from job_id or any other client input — see the
# route handlers below.
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
INDEX_HTML_PATH = STATIC_DIR / "index.html"
JOBS_HTML_PATH = STATIC_DIR / "jobs.html"


def _default_upload_root() -> Path:
    configured = os.getenv("CLOSEIQ_UPLOAD_ROOT")
    if configured:
        return Path(configured)
    # Outside the repository and outside any static/public path by
    # construction — nothing mounts the system temp directory.
    return Path(tempfile.gettempdir()) / "closeiq-uploads"


# Module-level so tests can override it (e.g. `patch("closeiq.api.UPLOAD_ROOT", ...)`).
UPLOAD_ROOT = _default_upload_root()


def _upload_dir(job_id: str) -> Path:
    return UPLOAD_ROOT / job_id


class ExceptionDecisionRequest(BaseModel):
    decision: Literal["acknowledge", "resolve", "dismiss"]
    note: str = Field(min_length=1)


DECISION_STATUSES = {
    "acknowledge": "reviewed",
    "resolve": "resolved",
    "dismiss": "dismissed",
}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def serve_upload_page() -> FileResponse:
    return FileResponse(INDEX_HTML_PATH)


@app.get("/jobs/{job_id}", include_in_schema=False)
def serve_job_page(job_id: str) -> FileResponse:
    """Serve the static job/results shell for any job_id.

    Deliberately does not touch the database or the filesystem beyond
    this one fixed file: job_id only shapes the URL so the page's own
    JavaScript can read it client-side and poll the real API (see
    static/app.js). An unknown job_id still gets this same 200 response;
    the "not found" state is rendered client-side from the API's 404 —
    see docs/phase_1b_spec.md, section 4 (WS-C) and the integration
    requirements this route implements.
    """
    del job_id  # Intentionally unused — see docstring.
    return FileResponse(JOBS_HTML_PATH)


def _latest_close_run_id(cursor) -> str | None:
    """The most recently created close run, or None if none exist yet.

    A close_runs row only ever exists for a job that reached `succeeded`
    (see close_jobs.run_close_job's atomic transaction), so "most recently
    created" and "most recently successful" are the same thing here.
    """
    cursor.execute(
        """
        SELECT close_run_id
        FROM close_runs
        ORDER BY created_at DESC, close_run_id DESC
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    return row[0] if row else None


@app.get("/exceptions")
def list_open_exceptions() -> list[dict[str, Any]]:
    """Open exceptions for the most recently created close run only.

    close_run_exceptions is run-scoped (see docs/closeiq_v2_architecture.md,
    section 3): the same exception "fingerprint" can be open in one run and
    resolved in another, so this endpoint does NOT aggregate across every
    historical run (that would be an ambiguous, ever-inflating count as
    more runs accumulate). To see a specific run's exceptions, use
    ``/close-runs/{close_run_id}/exceptions`` instead. Returns an empty
    list if no close run has completed yet.
    """
    with get_connection() as connection:
        with connection.cursor() as cursor:
            close_run_id = _latest_close_run_id(cursor)
            if close_run_id is None:
                return []

            cursor.execute(
                """
                SELECT
                    close_run_id,
                    exception_id,
                    exception_type,
                    severity,
                    status,
                    source_ids,
                    evidence
                FROM close_run_exceptions
                WHERE close_run_id = %s AND status = %s
                ORDER BY exception_id
                """,
                (close_run_id, "open"),
            )
            rows = cursor.fetchall()

    return [
        {
            "close_run_id": row[0],
            "exception_id": row[1],
            "exception_type": row[2],
            "severity": row[3],
            "status": row[4],
            "source_ids": row[5],
            "evidence": row[6],
        }
        for row in rows
    ]


@app.get("/close-summary")
def get_close_summary() -> dict[str, int]:
    """Exception status counts for the most recently created close run only.

    Does not aggregate across every historical run — see /exceptions above
    for why. Use ``/close-runs/{close_run_id}/close-summary`` for a
    specific run. Returns all-zero counts if no close run has completed
    yet.
    """
    summary = {
        "open": 0,
        "reviewed": 0,
        "resolved": 0,
        "dismissed": 0,
    }

    with get_connection() as connection:
        with connection.cursor() as cursor:
            close_run_id = _latest_close_run_id(cursor)
            if close_run_id is not None:
                cursor.execute(
                    """
                    SELECT status, COUNT(*)
                    FROM close_run_exceptions
                    WHERE close_run_id = %s
                    GROUP BY status
                    """,
                    (close_run_id,),
                )
                rows = cursor.fetchall()
                for status, count in rows:
                    summary[status] = count

    summary["total"] = sum(summary.values())
    return summary


@app.get("/close-runs/{close_run_id}/exceptions/{exception_id}/decisions")
def list_exception_decisions(
    close_run_id: str,
    exception_id: str,
) -> list[dict[str, Any]]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    decision_id,
                    close_run_id,
                    exception_id,
                    decision,
                    reviewer,
                    note,
                    decided_at
                FROM exception_decisions
                WHERE close_run_id = %s AND exception_id = %s
                ORDER BY decided_at DESC, decision_id DESC
                """,
                (close_run_id, exception_id),
            )
            rows = cursor.fetchall()

    return [
        {
            "decision_id": row[0],
            "close_run_id": row[1],
            "exception_id": row[2],
            "decision": row[3],
            "reviewer": row[4],
            "note": row[5],
            "decided_at": row[6],
        }
        for row in rows
    ]


@app.post(
    "/close-runs/{close_run_id}/exceptions/{exception_id}/decisions",
    status_code=201,
)
def create_exception_decision(
    close_run_id: str,
    exception_id: str,
    request: ExceptionDecisionRequest,
) -> dict[str, Any]:
    """Record a reviewer decision against exactly one run's exception.

    Scoped to (close_run_id, exception_id): recording a decision here
    never changes the status of the same exception_id "fingerprint" in
    any other close run (see docs/closeiq_v2_architecture.md, section 3).
    """
    exception_status = DECISION_STATUSES[request.decision]

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM close_run_exceptions
                WHERE close_run_id = %s AND exception_id = %s
                """,
                (close_run_id, exception_id),
            )
            if cursor.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="Close run exception not found",
                )

            cursor.execute(
                """
                INSERT INTO exception_decisions (
                    close_run_id,
                    exception_id,
                    decision,
                    reviewer,
                    note
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING
                    decision_id,
                    close_run_id,
                    exception_id,
                    decision,
                    reviewer,
                    note,
                    decided_at
                """,
                (
                    close_run_id,
                    exception_id,
                    request.decision,
                    "local-user",
                    request.note,
                ),
            )
            row = cursor.fetchone()

            cursor.execute(
                """
                UPDATE close_run_exceptions
                SET status = %s
                WHERE close_run_id = %s AND exception_id = %s
                """,
                (exception_status, close_run_id, exception_id),
            )

    return {
        "decision_id": row[0],
        "close_run_id": row[1],
        "exception_id": row[2],
        "decision": row[3],
        "reviewer": row[4],
        "note": row[5],
        "decided_at": row[6],
        "status": exception_status,
    }


def _infer_period_or_raise(journal_path: Path, bank_path: Path) -> str:
    """Call infer_close_period and map its typed errors onto the fixed
    422 contract in docs/phase_1b_spec.md, section 3.1.
    """
    try:
        return infer_close_period(journal_path, bank_path)
    except PeriodAmbiguousError as error:
        raise HTTPException(
            status_code=422,
            detail={"reason": "period_ambiguous", "months": error.months},
        ) from error
    except UnparseableDatesError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "unparseable_dates",
                "rows": [
                    {
                        "file": problem.file,
                        "row_number": problem.row_number,
                        "value": problem.value,
                        "reason": problem.reason,
                    }
                    for problem in error.problems
                ],
            },
        ) from error
    except EmptyFileError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "empty_file",
                "file": error.file,
                "message": str(error),
            },
        ) from error
    except MissingDateColumnError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "missing_date_column",
                "file": error.file,
                "message": str(error),
            },
        ) from error


def _run_job_in_background(job_id: str, job_dir: Path) -> None:
    """The BackgroundTasks target: run the job, always clean up its
    upload directory afterward — success or failure (see
    docs/phase_1b_spec.md, section 3.4). run_close_job already persists
    failure detail to close_jobs itself before re-raising; this only
    needs to keep that exception from propagating into the (nonexistent)
    caller of a background task.
    """
    journal_path = job_dir / JOURNAL_FILENAME
    bank_path = job_dir / BANK_FILENAME
    try:
        run_close_job(job_id, journal_path, bank_path)
    except Exception:
        logger.exception("Close job %s failed", job_id)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@app.post("/close-runs", status_code=202)
async def create_close_run_from_upload(
    background_tasks: BackgroundTasks,
    close_period: str | None = Form(default=None),
    journal_file: UploadFile = File(...),
    bank_file: UploadFile = File(...),
) -> dict[str, Any]:
    """Create a close job and run it in the background; return
    immediately. See docs/phase_1b_spec.md, sections 2 and 3, for the
    full design this implements.
    """
    if not journal_file.filename or not bank_file.filename:
        raise HTTPException(
            status_code=422,
            detail="Both uploaded files must have filenames",
        )

    if (
        Path(journal_file.filename).suffix.lower() != ".csv"
        or Path(bank_file.filename).suffix.lower() != ".csv"
    ):
        raise HTTPException(
            status_code=422,
            detail="Journal and bank uploads must be CSV files",
        )

    # Display-safe only: recorded on the job for a human to read, never
    # used as a filesystem path (see docs/phase_1b_spec.md, section 3.4).
    journal_source = Path(journal_file.filename).name
    bank_source = Path(bank_file.filename).name

    try:
        journal_bytes = await journal_file.read()
        bank_bytes = await bank_file.read()
    finally:
        await journal_file.close()
        await bank_file.close()

    # Staged under a throwaway id first: the real job_id (and therefore
    # its permanent directory name) isn't known until create_close_job
    # succeeds, which itself needs these files on disk for period
    # inference when close_period is omitted.
    staging_dir = _upload_dir(f"staging-{uuid4()}")
    staging_dir.mkdir(parents=True, exist_ok=True)
    journal_path = staging_dir / JOURNAL_FILENAME
    bank_path = staging_dir / BANK_FILENAME

    try:
        journal_path.write_bytes(journal_bytes)
        bank_path.write_bytes(bank_bytes)

        if close_period is None:
            close_period = _infer_period_or_raise(journal_path, bank_path)

        try:
            job_id = create_close_job(
                close_period,
                journal_source=journal_source,
                bank_source=bank_source,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail=str(error),
            ) from error

        job_dir = _upload_dir(job_id)
        staging_dir.rename(job_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    background_tasks.add_task(_run_job_in_background, job_id, job_dir)

    return {"job_id": job_id, "status": "queued"}


@app.get("/close-jobs/{job_id}")
def get_close_job_status(job_id: str) -> dict[str, Any]:
    job = get_close_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Close job not found")
    return job


# FastAPI's BackgroundTasks (used above) is local, in-process, and not
# durable across a restart — see docs/phase_1b_spec.md, section 2.1. A
# job can only be left `queued`/`running` here because a prior process
# died before finishing it; nothing else in this system leaves a job in
# either state with no code actively executing it.
STALE_JOB_ERROR_DETAIL = (
    "Interrupted by an application restart before this run finished. "
    "Please upload your files again."
)


def _recover_stale_jobs() -> None:
    """Mark every job left queued/running by a prior process as failed.

    The error detail is a fixed, safe, plain-language string — never a
    stack trace, filesystem path, database credential, or raw SQL.
    """
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE close_jobs
                SET
                    status = 'failed',
                    error_detail = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('queued', 'running')
                """,
                (STALE_JOB_ERROR_DETAIL,),
            )


def _cleanup_abandoned_upload_directories() -> None:
    """Remove every job-owned upload directory left over at startup.

    Must run after `_recover_stale_jobs`: by then every previously
    queued/running job has already been marked failed, so nothing in
    this fresh process still needs any upload directory that happens to
    exist — each one can only belong to a job from a prior process that
    no longer has code running to clean it up itself.
    """
    if not UPLOAD_ROOT.exists():
        return
    for entry in UPLOAD_ROOT.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)


@app.on_event("startup")
def _recover_on_startup() -> None:
    _recover_stale_jobs()
    _cleanup_abandoned_upload_directories()


@app.get("/close-runs")
def list_close_runs() -> list[dict[str, Any]]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    close_run_id,
                    close_period,
                    journal_source,
                    bank_source,
                    imported_journal_line_count,
                    imported_bank_transaction_count,
                    total_exception_count,
                    created_at
                FROM close_runs
                ORDER BY close_period DESC, created_at DESC, close_run_id DESC
                """
            )
            rows = cursor.fetchall()

    return [
        {
            "close_run_id": row[0],
            "close_period": row[1],
            "journal_source": row[2],
            "bank_source": row[3],
            "imported_journal_line_count": row[4],
            "imported_bank_transaction_count": row[5],
            "total_exception_count": row[6],
            "created_at": row[7],
        }
        for row in rows
    ]


def _require_close_run(cursor, close_run_id: str) -> None:
    cursor.execute(
        """
        SELECT 1
        FROM close_runs
        WHERE close_run_id = %s
        """,
        (close_run_id,),
    )
    if cursor.fetchone() is None:
        raise HTTPException(
            status_code=404,
            detail="Close run not found",
        )


@app.get("/close-runs/{close_run_id}/exceptions")
def list_close_run_exceptions(
    close_run_id: str,
) -> list[dict[str, Any]]:
    """That run's open exceptions only — unambiguously scoped to one run.

    Unknown close_run_id returns 404.
    """
    with get_connection() as connection:
        with connection.cursor() as cursor:
            _require_close_run(cursor, close_run_id)

            cursor.execute(
                """
                SELECT
                    exception_id,
                    exception_type,
                    severity,
                    status,
                    source_ids,
                    evidence,
                    created_at
                FROM close_run_exceptions
                WHERE close_run_id = %s AND status = %s
                ORDER BY exception_id
                """,
                (close_run_id, "open"),
            )
            rows = cursor.fetchall()

    return [
        {
            "exception_id": row[0],
            "exception_type": row[1],
            "severity": row[2],
            "status": row[3],
            "source_ids": row[4],
            "evidence": row[5],
            "created_at": row[6],
        }
        for row in rows
    ]


@app.get("/close-runs/{close_run_id}/close-summary")
def get_close_run_summary(close_run_id: str) -> dict[str, int]:
    """Exception status counts for exactly one close run.

    Unknown close_run_id returns 404.
    """
    summary = {
        "open": 0,
        "reviewed": 0,
        "resolved": 0,
        "dismissed": 0,
    }

    with get_connection() as connection:
        with connection.cursor() as cursor:
            _require_close_run(cursor, close_run_id)

            cursor.execute(
                """
                SELECT status, COUNT(*)
                FROM close_run_exceptions
                WHERE close_run_id = %s
                GROUP BY status
                """,
                (close_run_id,),
            )
            rows = cursor.fetchall()

    for status, count in rows:
        summary[status] = count

    summary["total"] = sum(summary.values())
    return summary


# Mounted last, under its own /static prefix only — this never shadows
# any API route above (all of which live outside /static), and it is not
# a catch-all at "/". Starlette's StaticFiles rejects any path that
# would escape STATIC_DIR (e.g. "..", absolute paths), so this cannot be
# used for path traversal outside the static directory. See
# docs/phase_1b_spec.md, section 4 (WS-C), and the integration
# requirements this implements.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")