from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .close_run import run_close
from .database import get_connection


app = FastAPI(
    title="CloseIQ API",
    version="0.1.0",
)


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


@app.get("/exceptions")
def list_open_exceptions() -> list[dict[str, Any]]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    exception_id,
                    exception_type,
                    severity,
                    status,
                    source_ids,
                    evidence
                FROM close_exceptions
                WHERE status = %s
                ORDER BY exception_id
                """,
                ("open",),
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
        }
        for row in rows
    ]


@app.get("/close-summary")
def get_close_summary() -> dict[str, int]:
    summary = {
        "open": 0,
        "reviewed": 0,
        "resolved": 0,
        "dismissed": 0,
    }

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, COUNT(*)
                FROM close_exceptions
                GROUP BY status
                """
            )
            rows = cursor.fetchall()

    for status, count in rows:
        summary[status] = count

    summary["total"] = sum(summary.values())
    return summary


@app.get("/exceptions/{exception_id}/decisions")
def list_exception_decisions(
    exception_id: str,
) -> list[dict[str, Any]]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    decision_id,
                    exception_id,
                    decision,
                    reviewer,
                    note,
                    decided_at
                FROM exception_decisions
                WHERE exception_id = %s
                ORDER BY decided_at DESC, decision_id DESC
                """,
                (exception_id,),
            )
            rows = cursor.fetchall()

    return [
        {
            "decision_id": row[0],
            "exception_id": row[1],
            "decision": row[2],
            "reviewer": row[3],
            "note": row[4],
            "decided_at": row[5],
        }
        for row in rows
    ]


@app.post("/exceptions/{exception_id}/decisions", status_code=201)
def create_exception_decision(
    exception_id: str,
    request: ExceptionDecisionRequest,
) -> dict[str, Any]:
    exception_status = DECISION_STATUSES[request.decision]

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM close_exceptions
                WHERE exception_id = %s
                """,
                (exception_id,),
            )
            if cursor.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="Close exception not found",
                )

            cursor.execute(
                """
                INSERT INTO exception_decisions (
                    exception_id,
                    decision,
                    reviewer,
                    note
                )
                VALUES (%s, %s, %s, %s)
                RETURNING
                    decision_id,
                    exception_id,
                    decision,
                    reviewer,
                    note,
                    decided_at
                """,
                (
                    exception_id,
                    request.decision,
                    "local-user",
                    request.note,
                ),
            )
            row = cursor.fetchone()

            cursor.execute(
                """
                UPDATE close_exceptions
                SET status = %s
                WHERE exception_id = %s
                """,
                (exception_status, exception_id),
            )

    return {
        "decision_id": row[0],
        "exception_id": row[1],
        "decision": row[2],
        "reviewer": row[3],
        "note": row[4],
        "decided_at": row[5],
        "status": exception_status,
    }


@app.post("/close-runs", status_code=201)
async def create_close_run_from_upload(
    close_period: str = Form(...),
    journal_file: UploadFile = File(...),
    bank_file: UploadFile = File(...),
) -> dict[str, Any]:
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

    try:
        with TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            journal_path = temporary_path / "journal_entries.csv"
            bank_path = temporary_path / "bank_transactions.csv"

            journal_path.write_bytes(await journal_file.read())
            bank_path.write_bytes(await bank_file.read())

            return run_close(
                journal_path,
                bank_path,
                close_period=close_period,
                journal_source=journal_file.filename,
                bank_source=bank_file.filename,
            )
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error
    finally:
        await journal_file.close()
        await bank_file.close()


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


@app.get("/close-runs/{close_run_id}/exceptions")
def list_close_run_exceptions(
    close_run_id: str,
) -> list[dict[str, Any]]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
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
                WHERE close_run_id = %s
                ORDER BY exception_id
                """,
                (close_run_id,),
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