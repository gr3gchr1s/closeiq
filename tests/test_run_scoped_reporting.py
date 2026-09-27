import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.api import app
from closeiq.close_jobs import create_close_job, run_close_job
from closeiq.database import get_connection


PROJECT_ROOT = Path(__file__).parents[1]
JOURNAL_FILE = PROJECT_ROOT / "data" / "journal_entries.csv"
BANK_FILE = PROJECT_ROOT / "data" / "bank_transactions.csv"

# Present in every run of the sample data (see data/journal_entries.csv).
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


class RunScopedReportingTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
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

    def test_run_scoped_and_default_endpoints_do_not_mix_run_data(self):
        # Run A: 4 open exceptions, none reviewed yet.
        run_a = self._run()
        run_a_id = run_a["close_run_id"]

        # Run B: same source data (so the same 4 exception fingerprints
        # recur), but one of them gets resolved — Run A and Run B now have
        # different status distributions.
        run_b = self._run()
        run_b_id = run_b["close_run_id"]
        self.assertNotEqual(run_a_id, run_b_id)

        decision_response = self.client.post(
            f"/close-runs/{run_b_id}/exceptions/{RECURRING_EXCEPTION_ID}/decisions",
            json={"decision": "resolve", "note": "Resolved for run B only."},
        )
        self.assertEqual(decision_response.status_code, 201)

        # --- Run A and Run B have different status distributions. ---
        run_a_summary = self.client.get(
            f"/close-runs/{run_a_id}/close-summary"
        ).json()
        run_b_summary = self.client.get(
            f"/close-runs/{run_b_id}/close-summary"
        ).json()

        self.assertEqual(run_a_summary["open"], 4)
        self.assertEqual(run_a_summary["resolved"], 0)
        self.assertEqual(run_a_summary["total"], 4)

        self.assertEqual(run_b_summary["open"], 3)
        self.assertEqual(run_b_summary["resolved"], 1)
        self.assertEqual(run_b_summary["total"], 4)

        # --- Run-scoped endpoints return only their requested run's data. ---
        run_a_exceptions = self.client.get(
            f"/close-runs/{run_a_id}/exceptions"
        ).json()
        run_b_exceptions = self.client.get(
            f"/close-runs/{run_b_id}/exceptions"
        ).json()

        self.assertEqual(len(run_a_exceptions), 4)
        self.assertEqual(len(run_b_exceptions), 3)  # resolved one is excluded
        self.assertTrue(
            all(e["status"] == "open" for e in run_a_exceptions)
        )
        self.assertTrue(
            all(e["status"] == "open" for e in run_b_exceptions)
        )
        self.assertNotIn(
            RECURRING_EXCEPTION_ID,
            [e["exception_id"] for e in run_b_exceptions],
        )
        self.assertIn(
            RECURRING_EXCEPTION_ID,
            [e["exception_id"] for e in run_a_exceptions],
        )

        # --- Default legacy endpoints return the latest run (B) only,
        #     not Run A + Run B combined. ---
        default_summary = self.client.get("/close-summary").json()
        self.assertEqual(default_summary, run_b_summary)
        self.assertNotEqual(
            default_summary["total"],
            run_a_summary["total"] + run_b_summary["total"],
        )

        default_exceptions = self.client.get("/exceptions").json()
        self.assertEqual(len(default_exceptions), len(run_b_exceptions))
        self.assertEqual(
            {e["exception_id"] for e in default_exceptions},
            {e["exception_id"] for e in run_b_exceptions},
        )
        for exception in default_exceptions:
            self.assertEqual(exception["close_run_id"], run_b_id)

    def test_unknown_close_run_id_returns_404_on_run_scoped_endpoints(self):
        exceptions_response = self.client.get(
            "/close-runs/does-not-exist/exceptions"
        )
        summary_response = self.client.get(
            "/close-runs/does-not-exist/close-summary"
        )

        self.assertEqual(exceptions_response.status_code, 404)
        self.assertEqual(summary_response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
