import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.close_run import run_close
from closeiq.database import get_connection


PROJECT_ROOT = Path(__file__).parents[1]


class CloseRunTest(unittest.TestCase):
    def test_run_close_imports_data_and_persists_exceptions(self):
        result = run_close(
            PROJECT_ROOT / "data" / "journal_entries.csv",
            PROJECT_ROOT / "data" / "bank_transactions.csv",
        )

        self.assertEqual(result["imported_journal_line_count"], 9)
        self.assertEqual(result["imported_bank_transaction_count"], 4)
        self.assertEqual(
            result["close_review"]["summary"]["total_exception_count"],
            4,
        )

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM close_exceptions")
                exception_count = cursor.fetchone()[0]

        self.assertEqual(exception_count, 4)


if __name__ == "__main__":
    unittest.main()