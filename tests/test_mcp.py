import sys
import unittest
from pathlib import Path

from mcp import Client

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.close_jobs import create_close_job
from closeiq.database import get_connection
from closeiq.mcp_server import mcp

class CloseIQMcpTest(unittest.IsolatedAsyncioTestCase):
    async def test_close_summary_tool_returns_status_counts(self):
        async with Client(mcp) as client:
            result = await client.call_tool("close_summary", {})

        summary = result.structured_content

        self.assertEqual(
            set(summary),
            {"open", "reviewed", "resolved", "dismissed", "total"},
        )
        self.assertEqual(
            summary["total"],
            summary["open"]
            + summary["reviewed"]
            + summary["resolved"]
            + summary["dismissed"],
        )

    async def test_list_open_exceptions_tool_returns_review_evidence(self):
        async with Client(mcp) as client:
            result = await client.call_tool("list_open_exceptions", {})

        exceptions = result.structured_content["exceptions"]

        self.assertIsInstance(exceptions, list)
        self.assertGreater(len(exceptions), 0)

        for exception in exceptions:
            self.assertEqual(exception["status"], "open")
            self.assertIn("close_run_id", exception)
            self.assertIn("exception_id", exception)
            self.assertIn("exception_type", exception)
            self.assertIn("severity", exception)
            self.assertIn("source_ids", exception)
            self.assertIn("evidence", exception)

    async def test_decision_history_tool_returns_audit_trail(self):
        exception_id = "mcp-test:decision-history"
        job_id = create_close_job(
            "2026-08",
            journal_source="mcp-test-journal.csv",
            bank_source="mcp-test-bank.csv",
        )
        close_run_id = "mcp-test:close-run"

        with get_connection() as connection:
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
                        "2026-08",
                        "mcp-test-journal.csv",
                        "mcp-test-bank.csv",
                        0,
                        0,
                        1,
                        job_id,
                    ),
                )
                cursor.execute(
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
                    (
                        close_run_id,
                        exception_id,
                        "mcp_test",
                        "low",
                        "open",
                        [exception_id],
                        "{}",
                    ),
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
                    """,
                    (
                        close_run_id,
                        exception_id,
                        "acknowledge",
                        "mcp-test-reviewer",
                        "Created only for the MCP audit-trail test.",
                    ),
                )

        try:
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "exception_decision_history",
                    {"close_run_id": close_run_id, "exception_id": exception_id},
                )

            decisions = result.structured_content["decisions"]

            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["close_run_id"], close_run_id)
            self.assertEqual(decisions[0]["exception_id"], exception_id)
            self.assertEqual(decisions[0]["decision"], "acknowledge")
            self.assertEqual(decisions[0]["reviewer"], "mcp-test-reviewer")
            self.assertIn("decided_at", decisions[0])
        finally:
            with get_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM exception_decisions WHERE close_run_id = %s",
                        (close_run_id,),
                    )
                    cursor.execute(
                        "DELETE FROM close_run_exceptions WHERE close_run_id = %s",
                        (close_run_id,),
                    )
                    cursor.execute(
                        "UPDATE close_jobs SET close_run_id = NULL WHERE job_id = %s",
                        (job_id,),
                    )
                    cursor.execute(
                        "DELETE FROM close_runs WHERE close_run_id = %s",
                        (close_run_id,),
                    )
                    cursor.execute(
                        "DELETE FROM close_jobs WHERE job_id = %s",
                        (job_id,),
                    )

if __name__ == "__main__":
    unittest.main()