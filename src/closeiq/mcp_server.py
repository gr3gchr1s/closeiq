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
    """Return all open CloseIQ exceptions with their review evidence.

    Exceptions are run-scoped (see docs/closeiq_v2_architecture.md,
    section 3): the same exception can be open in one close run and
    resolved in another, so each result carries its own close_run_id.
    """
    with get_connection() as connection:
        with connection.cursor() as cursor:
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
                WHERE status = 'open'
                ORDER BY created_at, close_run_id, exception_id
                """
            )
            rows = cursor.fetchall()

    return {
        "exceptions": [
            {
                "close_run_id": close_run_id,
                "exception_id": exception_id,
                "exception_type": exception_type,
                "severity": severity,
                "status": status,
                "source_ids": source_ids,
                "evidence": evidence,
            }
            for (
                close_run_id,
                exception_id,
                exception_type,
                severity,
                status,
                source_ids,
                evidence,
            ) in rows
        ]
    }


@mcp.tool()
def exception_decision_history(
    close_run_id: str,
    exception_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Return the read-only reviewer decision history for one run's
    exception (close_run_id, exception_id) — see list_open_exceptions.
    """
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
                ORDER BY decided_at, decision_id
                """,
                (close_run_id, exception_id),
            )
            rows = cursor.fetchall()

    return {
        "decisions": [
            {
                "decision_id": decision_id,
                "close_run_id": decision_close_run_id,
                "exception_id": decision_exception_id,
                "decision": decision,
                "reviewer": reviewer,
                "note": note,
                "decided_at": decided_at.isoformat(),
            }
            for (
                decision_id,
                decision_close_run_id,
                decision_exception_id,
                decision,
                reviewer,
                note,
                decided_at,
            ) in rows
        ]
    }