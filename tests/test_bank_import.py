import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.bank_import import import_bank_transactions
from closeiq.close_jobs import create_close_job
from closeiq.database import get_connection


PROJECT_ROOT = Path(__file__).parents[1]


class BankImportTest(unittest.TestCase):
    def setUp(self):
        # import_bank_transactions now requires an import_batch_id that
        # references an existing close_jobs row (see
        # docs/closeiq_v2_architecture.md, section 3), so a real job is
        # created here to stand in for the job that would normally own
        # this import.
        self.job_id = create_close_job(
            "2026-08",
            journal_source="journal_entries.csv",
            bank_source="bank_transactions.csv",
        )

    def tearDown(self):
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM bank_transactions WHERE import_batch_id = %s",
                    (self.job_id,),
                )
                cursor.execute(
                    "DELETE FROM close_jobs WHERE job_id = %s",
                    (self.job_id,),
                )

    def test_import_bank_transactions_loads_data_into_postgres(self):
        imported_count = import_bank_transactions(
            PROJECT_ROOT / "data" / "bank_transactions.csv",
            import_batch_id=self.job_id,
        )

        self.assertEqual(imported_count, 4)

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT amount, external_reference
                    FROM bank_transactions
                    WHERE import_batch_id = %s AND bank_transaction_id = %s
                    """,
                    (self.job_id, "BT-8104"),
                )
                transaction = cursor.fetchone()

        self.assertEqual(
            transaction,
            (Decimal("-12.00"), "FEE-0819"),
        )


if __name__ == "__main__":
    unittest.main()
