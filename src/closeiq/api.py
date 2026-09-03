from typing import Any

from fastapi import FastAPI

from .database import get_connection


app = FastAPI(
    title="CloseIQ API",
    version="0.1.0",
)


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