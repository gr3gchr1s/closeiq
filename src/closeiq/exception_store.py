from __future__ import annotations

from typing import Any

from psycopg.types.json import Json

from .database import get_connection


def upsert_close_exceptions(
    exceptions: list[dict[str, object]],
) -> int:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            for exception in exceptions:
                source_ids = [
                    str(source_id)
                    for source_id in exception["source_ids"]
                ]

                evidence = {
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
                }

                cursor.execute(
                    """
                    INSERT INTO close_exceptions (
                        exception_id,
                        exception_type,
                        severity,
                        status,
                        source_ids,
                        evidence
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (exception_id)
                    DO UPDATE SET
                        exception_type = EXCLUDED.exception_type,
                        severity = EXCLUDED.severity,
                        source_ids = EXCLUDED.source_ids,
                        evidence = EXCLUDED.evidence
                    """,
                    (
                        exception["exception_id"],
                        exception["exception_type"],
                        exception["severity"],
                        exception["status"],
                        source_ids,
                        Json(evidence),
                    ),
                )

    return len(exceptions)