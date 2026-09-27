import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.close_jobs import create_close_job
from closeiq.database import get_connection
from closeiq.journal_import import import_journal_entries
from closeiq.seed_accounts import seed_accounts


PROJECT_ROOT = Path(__file__).parents[1]


class JournalImportTest(unittest.TestCase):
    def setUp(self):
        # import_journal_entries now requires an import_batch_id that
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
                    "DELETE FROM journal_lines WHERE import_batch_id = %s",
                    (self.job_id,),
                )
                cursor.execute(
                    "DELETE FROM journal_entries WHERE import_batch_id = %s",
                    (self.job_id,),
                )
                cursor.execute(
                    "DELETE FROM close_jobs WHERE job_id = %s",
                    (self.job_id,),
                )

    def test_import_journal_entries_loads_lines_into_postgres(self):
        seed_accounts(PROJECT_ROOT / "data" / "chart_of_accounts.csv")

        imported_count = import_journal_entries(
            PROJECT_ROOT / "data" / "journal_entries.csv",
            import_batch_id=self.job_id,
        )

        self.assertEqual(imported_count, 9)

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM journal_lines
                    WHERE import_batch_id = %s AND journal_id = %s
                    """,
                    (self.job_id, "JE-1005"),
                )
                line_count = cursor.fetchone()[0]

        self.assertEqual(line_count, 2)


if __name__ == "__main__":
    unittest.main()
