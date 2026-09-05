from typing import Any

from mcp.server import MCPServer

from closeiq.api import get_close_summary
from closeiq.database import get_connection


mcp = MCPServer("CloseIQ")


@mcp.tool()
def close_summary() -> dict[str, int]:
    """Return read-only CloseIQ exception counts by workflow status."""
    return get_close_summary()


@mcp.tool()
def list_open_exceptions() -> dict[str, list[dict[str, Any]]]:
    """Return all open CloseIQ exceptions with their review evidence."""
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
                WHERE status = 'open'
                ORDER BY created_at, exception_id
                """
            )
            rows = cursor.fetchall()

    return {
        "exceptions": [
            {
                "exception_id": exception_id,
                "exception_type": exception_type,
                "severity": severity,
                "status": status,
                "source_ids": source_ids,
                "evidence": evidence,
            }
            for (
                exception_id,
                exception_type,
                severity,
                status,
                source_ids,
                evidence,
            ) in rows
        ]
    }