import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.database import get_connection
from closeiq.journal_import import import_journal_entries
from closeiq.seed_accounts import seed_accounts


PROJECT_ROOT = Path(__file__).parents[1]


class JournalImportTest(unittest.TestCase):
    def test_import_journal_entries_loads_lines_into_postgres(self):
        seed_accounts(PROJECT_ROOT / "data" / "chart_of_accounts.csv")

        imported_count = import_journal_entries(
            PROJECT_ROOT / "data" / "journal_entries.csv"
        )

        self.assertEqual(imported_count, 9)

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM journal_lines
                    WHERE journal_id = %s
                    """,
                    ("JE-1005",),
                )
                line_count = cursor.fetchone()[0]

        self.assertEqual(line_count, 2)


if __name__ == "__main__":
    unittest.main()