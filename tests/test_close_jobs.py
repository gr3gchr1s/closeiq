import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq import close_jobs
from closeiq.close_jobs import (
    create_close_job,
    get_close_job,
    run_close_job,
)
from closeiq.database import get_connection


PROJECT_ROOT = Path(__file__).parents[1]
JOURNAL_FILE = PROJECT_ROOT / "data" / "journal_entries.csv"
BANK_FILE = PROJECT_ROOT / "data" / "bank_transactions.csv"


def _rows_for_batch(job_id: str) -> dict[str, int]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM journal_entries WHERE import_batch_id = %s",
                (job_id,),
            )
            journal_entries = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM journal_lines WHERE import_batch_id = %s",
                (job_id,),
            )
            journal_lines = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM bank_transactions WHERE import_batch_id = %s",
                (job_id,),
            )
            bank_transactions = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM close_runs WHERE import_batch_id = %s",
                (job_id,),
            )
            close_runs = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM close_run_exceptions
                WHERE close_run_id IN (
                    SELECT close_run_id FROM close_runs
                    WHERE import_batch_id = %s
                )
                """,
                (job_id,),
            )
            close_run_exceptions = cursor.fetchone()[0]

    return {
        "journal_entries": journal_entries,
        "journal_lines": journal_lines,
        "bank_transactions": bank_transactions,
        "close_runs": close_runs,
        "close_run_exceptions": close_run_exceptions,
    }


def _delete_batch(job_id: str) -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM close_run_exceptions
                WHERE close_run_id IN (
                    SELECT close_run_id FROM close_runs
                    WHERE import_batch_id = %s
                )
                """,
                (job_id,),
            )
            # close_jobs.close_run_id and close_runs.import_batch_id
            # reference each other, so the cycle must be broken before
            # either row can be deleted.
            cursor.execute(
                "UPDATE close_jobs SET close_run_id = NULL WHERE job_id = %s",
                (job_id,),
            )
            cursor.execute(
                "DELETE FROM close_runs WHERE import_batch_id = %s",
                (job_id,),
            )
            cursor.execute(
                "DELETE FROM journal_lines WHERE import_batch_id = %s",
                (job_id,),
            )
            cursor.execute(
                "DELETE FROM journal_entries WHERE import_batch_id = %s",
                (job_id,),
            )
            cursor.execute(
                "DELETE FROM bank_transactions WHERE import_batch_id = %s",
                (job_id,),
            )
            cursor.execute(
                "DELETE FROM close_jobs WHERE job_id = %s",
                (job_id,),
            )


class CloseJobLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.job_ids: list[str] = []

    def tearDown(self):
        for job_id in self.job_ids:
            _delete_batch(job_id)

    def _create_job(self, **overrides) -> str:
        job_id = create_close_job(
            overrides.get("close_period", "2026-08"),
            journal_source=overrides.get("journal_source", "journal_entries.csv"),
            bank_source=overrides.get("bank_source", "bank_transactions.csv"),
        )
        self.job_ids.append(job_id)
        return job_id

    def test_job_reaches_succeeded_via_running_with_a_populated_close_run_id(self):
        job_id = self._create_job()

        self.assertEqual(get_close_job(job_id)["status"], "queued")

        observed_status_during_run = {}

        original_build_close_review = close_jobs.build_close_review

        def _spy_build_close_review(*args, **kwargs):
            observed_status_during_run["status"] = get_close_job(job_id)["status"]
            return original_build_close_review(*args, **kwargs)

        with patch.object(
            close_jobs, "build_close_review", side_effect=_spy_build_close_review
        ):
            result = run_close_job(job_id, JOURNAL_FILE, BANK_FILE)

        self.assertEqual(observed_status_during_run["status"], "running")

        job = get_close_job(job_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertIsNotNone(job["close_run_id"])
        self.assertEqual(job["close_run_id"], result["close_run_id"])
        self.assertIsNone(job["error_detail"])

    def test_two_jobs_on_the_same_source_ids_create_independent_auditable_runs(self):
        first_job_id = self._create_job()
        second_job_id = self._create_job()

        first_result = run_close_job(first_job_id, JOURNAL_FILE, BANK_FILE)
        second_result = run_close_job(second_job_id, JOURNAL_FILE, BANK_FILE)

        self.assertNotEqual(
            first_result["close_run_id"], second_result["close_run_id"]
        )

        first_rows = _rows_for_batch(first_job_id)
        second_rows = _rows_for_batch(second_job_id)

        # Same source CSVs (same journal_id/bank_transaction_id values in
        # both), but neither batch's rows were overwritten by the other.
        self.assertEqual(first_rows["journal_entries"], 5)
        self.assertEqual(second_rows["journal_entries"], 5)
        self.assertEqual(first_rows["journal_lines"], 9)
        self.assertEqual(second_rows["journal_lines"], 9)
        self.assertEqual(first_rows["bank_transactions"], 4)
        self.assertEqual(second_rows["bank_transactions"], 4)
        self.assertEqual(first_rows["close_runs"], 1)
        self.assertEqual(second_rows["close_runs"], 1)

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT journal_date FROM journal_entries
                    WHERE import_batch_id = %s AND journal_id = 'JE-1004'
                    """,
                    (first_job_id,),
                )
                first_je_1004 = cursor.fetchone()

                cursor.execute(
                    """
                    SELECT journal_date FROM journal_entries
                    WHERE import_batch_id = %s AND journal_id = 'JE-1004'
                    """,
                    (second_job_id,),
                )
                second_je_1004 = cursor.fetchone()

        self.assertIsNotNone(first_je_1004)
        self.assertIsNotNone(second_je_1004)
        self.assertEqual(first_je_1004, second_je_1004)

    def test_later_batch_does_not_affect_earlier_batchs_exceptions_or_snapshots(self):
        first_job_id = self._create_job()
        first_result = run_close_job(first_job_id, JOURNAL_FILE, BANK_FILE)

        first_rows_before = _rows_for_batch(first_job_id)

        second_job_id = self._create_job()
        run_close_job(second_job_id, JOURNAL_FILE, BANK_FILE)

        first_rows_after = _rows_for_batch(first_job_id)

        self.assertEqual(first_rows_before, first_rows_after)

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT exception_id FROM close_run_exceptions
                    WHERE close_run_id = %s
                    ORDER BY exception_id
                    """,
                    (first_result["close_run_id"],),
                )
                snapshot_exception_ids = {row[0] for row in cursor.fetchall()}

        self.assertEqual(
            snapshot_exception_ids,
            {
                "journal-balance:JE-1004",
                "duplicate-reference:ACH-8102",
                "bank-reconciliation:BT-8102",
                "bank-reconciliation:BT-8104",
            },
        )

    def test_forced_failure_after_import_leaves_no_partial_rows_and_marks_job_failed(
        self,
    ):
        job_id = self._create_job()

        def _boom(*args, **kwargs):
            raise RuntimeError("forced failure for rollback test")

        with patch.object(close_jobs, "build_close_review", side_effect=_boom):
            with self.assertRaises(RuntimeError):
                run_close_job(job_id, JOURNAL_FILE, BANK_FILE)

        job = get_close_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIsNone(job["close_run_id"])
        self.assertIn("forced failure for rollback test", job["error_detail"])

        rows = _rows_for_batch(job_id)
        self.assertEqual(
            rows,
            {
                "journal_entries": 0,
                "journal_lines": 0,
                "bank_transactions": 0,
                "close_runs": 0,
                "close_run_exceptions": 0,
            },
        )

    def test_run_close_job_rejects_unknown_or_non_queued_job(self):
        with self.assertRaises(ValueError):
            run_close_job("does-not-exist", JOURNAL_FILE, BANK_FILE)

        job_id = self._create_job()
        run_close_job(job_id, JOURNAL_FILE, BANK_FILE)

        with self.assertRaises(ValueError):
            run_close_job(job_id, JOURNAL_FILE, BANK_FILE)


if __name__ == "__main__":
    unittest.main()
