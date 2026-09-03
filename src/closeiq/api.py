from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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