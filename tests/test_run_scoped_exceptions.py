import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.close_jobs import create_close_job, run_close_job
from closeiq.database import get_connection


PROJECT_ROOT = Path(__file__).parents[1]
JOURNAL_FILE = PROJECT_ROOT / "data" / "journal_entries.csv"
BANK_FILE = PROJECT_ROOT / "data" / "bank_transactions.csv"

# Present in every run of the sample data (see data/journal_entries.csv):
# JE-1004 is a hand-crafted unbalanced entry, so this exception fingerprint
# recurs identically across independent runs of the same source files.
RECURRING_EXCEPTION_ID = "journal-balance:JE-1004"


def _delete_batch(job_id: str) -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM exception_decisions WHERE close_run_id IN ("
                "SELECT close_run_id FROM close_runs WHERE import_batch_id = %s)",
                (job_id,),
            )
            cursor.execute(
                "DELETE FROM close_run_exceptions WHERE close_run_id IN ("
                "SELECT close_run_id FROM close_runs WHERE import_batch_id = %s)",
                (job_id,),
            )
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


def _record_decision(
    close_run_id: str, exception_id: str, decision: str, reviewer: str, note: str
) -> None:
    status_by_decision = {
        "acknowledge": "reviewed",
        "resolve": "resolved",
        "dismiss": "dismissed",
    }
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO exception_decisions (
                    close_run_id, exception_id, decision, reviewer, note
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (close_run_id, exception_id, decision, reviewer, note),
            )
            cursor.execute(
                """
                UPDATE close_run_exceptions
                SET status = %s
                WHERE close_run_id = %s AND exception_id = %s
                """,
                (status_by_decision[decision], close_run_id, exception_id),
            )


def _exception_row(close_run_id: str, exception_id: str):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, source_ids, evidence
                FROM close_run_exceptions
                WHERE close_run_id = %s AND exception_id = %s
                """,
                (close_run_id, exception_id),
            )
            return cursor.fetchone()


def _decision_history(close_run_id: str, exception_id: str) -> list:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT decision, reviewer
                FROM exception_decisions
                WHERE close_run_id = %s AND exception_id = %s
                """,
                (close_run_id, exception_id),
            )
            return cursor.fetchall()


class RunScopedExceptionsTest(unittest.TestCase):
    def setUp(self):
        self.job_ids: list[str] = []

    def tearDown(self):
        for job_id in self.job_ids:
            _delete_batch(job_id)

    def _run(self) -> dict:
        job_id = create_close_job(
            "2026-08",
            journal_source="journal_entries.csv",
            bank_source="bank_transactions.csv",
        )
        self.job_ids.append(job_id)
        return run_close_job(job_id, JOURNAL_FILE, BANK_FILE)

    def test_decision_on_one_run_does_not_leak_into_a_later_identical_run(self):
        # 1. Create Run A, containing the known recurring exception.
        run_a = self._run()
        run_a_id = run_a["close_run_id"]

        row = _exception_row(run_a_id, RECURRING_EXCEPTION_ID)
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "open")

        # 2. Record a reviewer decision resolving Run A's exception.
        _record_decision(
            run_a_id,
            RECURRING_EXCEPTION_ID,
            "resolve",
            "reviewer-a",
            "Resolved for run A only.",
        )

        run_a_row_after_decision = _exception_row(run_a_id, RECURRING_EXCEPTION_ID)
        self.assertEqual(run_a_row_after_decision[0], "resolved")

        # 3. Create Run B with the same source files/IDs and same
        #    logical exception.
        run_b = self._run()
        run_b_id = run_b["close_run_id"]
        self.assertNotEqual(run_a_id, run_b_id)

        # 5. Run B's exception is independently open, with no inherited
        #    decision.
        run_b_row = _exception_row(run_b_id, RECURRING_EXCEPTION_ID)
        self.assertIsNotNone(run_b_row)
        self.assertEqual(run_b_row[0], "open")
        self.assertEqual(_decision_history(run_b_id, RECURRING_EXCEPTION_ID), [])

        # 4. Run A retains its decision/history after Run B completes.
        run_a_row_after_run_b = _exception_row(run_a_id, RECURRING_EXCEPTION_ID)
        self.assertEqual(run_a_row_after_run_b[0], "resolved")
        run_a_history = _decision_history(run_a_id, RECURRING_EXCEPTION_ID)
        self.assertEqual(len(run_a_history), 1)
        self.assertEqual(run_a_history[0], ("resolve", "reviewer-a"))

        # 6. Both runs' snapshots and source evidence remain available.
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM close_run_exceptions
                    WHERE close_run_id = %s
                    """,
                    (run_a_id,),
                )
                run_a_snapshot_count = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT COUNT(*) FROM close_run_exceptions
                    WHERE close_run_id = %s
                    """,
                    (run_b_id,),
                )
                run_b_snapshot_count = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT COUNT(*) FROM journal_entries
                    WHERE import_batch_id = %s AND journal_id = 'JE-1004'
                    """,
                    (run_a["job_id"],),
                )
                run_a_source_count = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT COUNT(*) FROM journal_entries
                    WHERE import_batch_id = %s AND journal_id = 'JE-1004'
                    """,
                    (run_b["job_id"],),
                )
                run_b_source_count = cursor.fetchone()[0]

        self.assertEqual(run_a_snapshot_count, 4)
        self.assertEqual(run_b_snapshot_count, 4)
        self.assertEqual(run_a_source_count, 1)
        self.assertEqual(run_b_source_count, 1)


if __name__ == "__main__":
    unittest.main()
