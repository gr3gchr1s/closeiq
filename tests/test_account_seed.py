import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.database import get_connection
from closeiq.seed_accounts import seed_accounts


PROJECT_ROOT = Path(__file__).parents[1]


class AccountSeedTest(unittest.TestCase):
    def test_seed_accounts_loads_chart_of_accounts_into_postgres(self):
        imported_count = seed_accounts(
            PROJECT_ROOT / "data" / "chart_of_accounts.csv"
        )

        self.assertEqual(imported_count, 8)

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT account_name, account_type, normal_balance
                    FROM accounts
                    WHERE account_code = %s
                    """,
                    ("6300",),
                )
                account = cursor.fetchone()

        self.assertEqual(
            account,
            ("Bank Fees Expense", "expense", "debit"),
        )


if __name__ == "__main__":
    unittest.main()
