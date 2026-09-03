import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.accounting import JournalLine
from closeiq.close_review import build_close_review


class ExceptionWorkflowTest(unittest.TestCase):
    def test_unbalanced_journal_has_review_workflow_metadata(self):
        lines = [
            JournalLine(
                "JE-1", "2026-08-01", "6100", "unbalanced expense",
                Decimal("25"), Decimal("0"), "EXP-1"
            )
        ]

        review = build_close_review(lines, [])
        exception = review["workflow_exceptions"][0]

        self.assertEqual(exception["exception_id"], "journal-balance:JE-1")
        self.assertEqual(exception["exception_type"], "journal_balance")
        self.assertEqual(exception["severity"], "high")
        self.assertEqual(exception["status"], "open")
        self.assertEqual(exception["source_ids"], ["JE-1"])


if __name__ == "__main__":
    unittest.main()