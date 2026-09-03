import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.bank_import import import_bank_transactions
from closeiq.database import get_connection


PROJECT_ROOT = Path(__file__).parents[1]


class BankImportTest(unittest.TestCase):
    def test_import_bank_transactions_loads_data_into_postgres(self):
        imported_count = import_bank_transactions(
            PROJECT_ROOT / "data" / "bank_transactions.csv"
        )

        self.assertEqual(imported_count, 4)

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT amount, external_reference
                    FROM bank_transactions
                    WHERE bank_transaction_id = %s
                    """,
                    ("BT-8104",),
                )
                transaction = cursor.fetchone()

        self.assertEqual(
            transaction,
            (Decimal("-12.00"), "FEE-0819"),
        )


if __name__ == "__main__":
    unittest.main()