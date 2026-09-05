import sys
import unittest
from pathlib import Path

from mcp import Client

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

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
            self.assertIn("exception_id", exception)
            self.assertIn("exception_type", exception)
            self.assertIn("severity", exception)
            self.assertIn("source_ids", exception)
            self.assertIn("evidence", exception)


if __name__ == "__main__":
    unittest.main()