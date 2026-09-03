import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.accounting import load_journal_lines
from closeiq.close_review import build_close_review
from closeiq.database import get_connection
from closeiq.exception_store import upsert_close_exceptions
from closeiq.reconciliation import load_bank_transactions


PROJECT_ROOT = Path(__file__).parents[1]


class ExceptionStoreTest(unittest.TestCase):
    def test_workflow_exceptions_are_saved_to_postgres(self):
        journal_lines = load_journal_lines(
            PROJECT_ROOT / "data" / "journal_entries.csv"
        )
        bank_transactions = load_bank_transactions(
            PROJECT_ROOT / "data" / "bank_transactions.csv"
        )

        review = build_close_review(journal_lines, bank_transactions)
        stored_count = upsert_close_exceptions(
            review["workflow_exceptions"]
        )

        self.assertEqual(stored_count, 4)

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT severity, status
                    FROM close_exceptions
                    WHERE exception_id = %s
                    """,
                    ("journal-balance:JE-1004",),
                )
                exception = cursor.fetchone()

        self.assertEqual(exception, ("high", "open"))


if __name__ == "__main__":
    unittest.main()