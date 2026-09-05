import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.close_run import run_close
from closeiq.database import get_connection


PROJECT_ROOT = Path(__file__).parents[1]


class CloseRunTest(unittest.TestCase):
    def test_run_close_records_period_aware_history(self):
        result = run_close(
            PROJECT_ROOT / "data" / "journal_entries.csv",
            PROJECT_ROOT / "data" / "bank_transactions.csv",
            close_period="2026-08",
        )

        self.assertEqual(result["close_period"], "2026-08")
        self.assertEqual(result["imported_journal_line_count"], 9)
        self.assertEqual(result["imported_bank_transaction_count"], 4)
        self.assertEqual(
            result["close_review"]["summary"]["total_exception_count"],
            4,
        )
        self.assertIn("close_run_id", result)

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM close_exceptions")
                exception_count = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT
                        close_period,
                        imported_journal_line_count,
                        imported_bank_transaction_count,
                        total_exception_count
                    FROM close_runs
                    WHERE close_run_id = %s
                    """,
                    (result["close_run_id"],),
                )
                close_run = cursor.fetchone()

        self.assertEqual(exception_count, 4)
        self.assertEqual(close_run, ("2026-08", 9, 4, 4))


if __name__ == "__main__":
    unittest.main()