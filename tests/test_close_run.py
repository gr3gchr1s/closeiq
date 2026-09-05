import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.close_run import run_close
from closeiq.database import get_connection


PROJECT_ROOT = Path(__file__).parents[1]


class CloseRunTest(unittest.TestCase):
    def test_run_close_records_period_aware_exception_snapshots(self):
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

        with get_connection() as connection:
            with connection.cursor() as cursor:
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

                cursor.execute(
                    """
                    SELECT
                        exception_id,
                        exception_type,
                        severity,
                        status,
                        source_ids,
                        evidence
                    FROM close_run_exceptions
                    WHERE close_run_id = %s
                    ORDER BY exception_id
                    """,
                    (result["close_run_id"],),
                )
                snapshots = cursor.fetchall()

        self.assertEqual(close_run, ("2026-08", 9, 4, 4))
        self.assertEqual(len(snapshots), 4)

        snapshots_by_id = {
            exception_id: {
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
            ) in snapshots
        }

        self.assertEqual(
            set(snapshots_by_id),
            {
                "journal-balance:JE-1004",
                "duplicate-reference:ACH-8102",
                "bank-reconciliation:BT-8102",
                "bank-reconciliation:BT-8104",
            },
        )

        journal_balance_snapshot = snapshots_by_id[
            "journal-balance:JE-1004"
        ]
        self.assertEqual(
            journal_balance_snapshot["exception_type"],
            "journal_balance",
        )
        self.assertEqual(journal_balance_snapshot["severity"], "high")
        self.assertEqual(journal_balance_snapshot["status"], "open")
        self.assertEqual(journal_balance_snapshot["source_ids"], ["JE-1004"])
        self.assertEqual(
            journal_balance_snapshot["evidence"]["reason"],
            "Journal entry is not balanced",
        )


if __name__ == "__main__":
    unittest.main()