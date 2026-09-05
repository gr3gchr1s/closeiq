import sys
import unittest
from pathlib import Path

from mcp import Client

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.mcp_server import mcp
from closeiq.database import get_connection

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
            self.assertIn("exception_id", exception)
            self.assertIn("exception_type", exception)
            self.assertIn("severity", exception)
            self.assertIn("source_ids", exception)
            self.assertIn("evidence", exception)

    async def test_decision_history_tool_returns_audit_trail(self):
        exception_id = "mcp-test:decision-history"

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM exception_decisions WHERE exception_id = %s",
                    (exception_id,),
                )
                cursor.execute(
                    "DELETE FROM close_exceptions WHERE exception_id = %s",
                    (exception_id,),
                )
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
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
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
                        exception_id,
                        decision,
                        reviewer,
                        note
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
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
                    {"exception_id": exception_id},
                )

            decisions = result.structured_content["decisions"]

            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["exception_id"], exception_id)
            self.assertEqual(decisions[0]["decision"], "acknowledge")
            self.assertEqual(decisions[0]["reviewer"], "mcp-test-reviewer")
            self.assertIn("decided_at", decisions[0])
        finally:
            with get_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM exception_decisions WHERE exception_id = %s",
                        (exception_id,),
                    )
                    cursor.execute(
                        "DELETE FROM close_exceptions WHERE exception_id = %s",
                        (exception_id,),
                    )

if __name__ == "__main__":
    unittest.main()