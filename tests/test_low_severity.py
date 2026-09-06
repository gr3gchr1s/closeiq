import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.accounting import load_journal_lines
from closeiq.close_review import build_close_review
from closeiq.reconciliation import load_bank_transactions


PROJECT_ROOT = Path(__file__).parents[1]


class LowSeverityControlTest(unittest.TestCase):
    def test_missing_bank_reference_is_a_low_severity_exception(self):
        journal_lines = load_journal_lines(
            PROJECT_ROOT / "data" / "demo_journal_entries.csv"
        )
        bank_transactions = load_bank_transactions(
            PROJECT_ROOT / "data" / "demo_bank_transactions.csv"
        )

        close_review = build_close_review(
            journal_lines,
            bank_transactions,
        )

        self.assertEqual(
            close_review["summary"]["total_exception_count"],
            5,
        )

        low_exceptions = [
            exception
            for exception in close_review["workflow_exceptions"]
            if exception["severity"] == "low"
        ]

        self.assertEqual(len(low_exceptions), 1)

        low_exception = low_exceptions[0]
        self.assertEqual(
            low_exception["exception_id"],
            "bank-reconciliation:BT-8109",
        )
        self.assertEqual(
            low_exception["exception_type"],
            "missing_bank_reference",
        )
        self.assertEqual(
            low_exception["reason"],
            "Bank transaction is missing an external reference",
        )


if __name__ == "__main__":
    unittest.main()