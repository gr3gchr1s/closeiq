import io
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq import api, close_jobs
from closeiq.database import get_connection


PROJECT_ROOT = Path(__file__).parents[1]
JOURNAL_FILE = PROJECT_ROOT / "data" / "journal_entries.csv"
BANK_FILE = PROJECT_ROOT / "data" / "bank_transactions.csv"

JOURNAL_HEADER = (
    "journal_id,date,account_code,description,debit,credit,external_reference"
)
BANK_HEADER = "transaction_id,date,description,amount,external_reference"


def _upload_files(journal_bytes: bytes, bank_bytes: bytes) -> dict:
    return {
        "journal_file": (
            "journal_entries.csv",
            io.BytesIO(journal_bytes),
            "text/csv",
        ),
        "bank_file": (
            "bank_transactions.csv",
            io.BytesIO(bank_bytes),
            "text/csv",
        ),
    }


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
                "DELETE FROM close_runs WHERE import_batch_id = %s", (job_id,)
            )
            cursor.execute(
                "DELETE FROM journal_lines WHERE import_batch_id = %s", (job_id,)
            )
            cursor.execute(
                "DELETE FROM journal_entries WHERE import_batch_id = %s", (job_id,)
            )
            cursor.execute(
                "DELETE FROM bank_transactions WHERE import_batch_id = %s",
                (job_id,),
            )
            cursor.execute("DELETE FROM close_jobs WHERE job_id = %s", (job_id,))


class CloseRunUploadTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)
        self.job_ids: list[str] = []

        self._upload_root_dir = TemporaryDirectory()
        self.addCleanup(self._upload_root_dir.cleanup)
        upload_root_patch = patch.object(
            api, "UPLOAD_ROOT", Path(self._upload_root_dir.name)
        )
        upload_root_patch.start()
        self.addCleanup(upload_root_patch.stop)

    def tearDown(self):
        for job_id in self.job_ids:
            _delete_batch(job_id)

    def _poll_job(self, job_id: str, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        job = None
        while time.monotonic() < deadline:
            job = self.client.get(f"/close-jobs/{job_id}").json()
            if job["status"] in ("succeeded", "failed"):
                return job
            time.sleep(0.02)
        raise AssertionError(f"job {job_id} did not reach a terminal state: {job}")

    # 1. Valid single-month files, no period -> 202 + job_id + queued.
    def test_upload_without_period_returns_202_queued_then_succeeds(self):
        response = self.client.post(
            "/close-runs",
            data={},
            files=_upload_files(JOURNAL_FILE.read_bytes(), BANK_FILE.read_bytes()),
        )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(set(body), {"job_id", "status"})
        self.assertEqual(body["status"], "queued")
        self.job_ids.append(body["job_id"])

        # 4. GET /close-jobs/{job_id} eventually returns succeeded + close_run_id.
        job = self._poll_job(body["job_id"])
        self.assertEqual(job["status"], "succeeded")
        self.assertIsNotNone(job["close_run_id"])
        self.assertEqual(job["close_period"], "2026-08")

    # 5. Explicit close_period still works (and bypasses inference).
    def test_explicit_close_period_bypasses_inference(self):
        response = self.client.post(
            "/close-runs",
            data={"close_period": "2099-01"},
            files=_upload_files(JOURNAL_FILE.read_bytes(), BANK_FILE.read_bytes()),
        )

        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        self.job_ids.append(job_id)

        job = self._poll_job(job_id)
        self.assertEqual(job["status"], "succeeded")
        # The sample data's actual dates are 2026-08; if this were
        # inferred instead of taken literally, it would say 2026-08.
        self.assertEqual(job["close_period"], "2099-01")

    # 2. Proves the endpoint schedules the runner rather than calling it
    #    inline. Uses mocking, not elapsed-time assumptions: TestClient
    #    runs a scheduled BackgroundTasks callable synchronously within
    #    the same client.post() call, so timing alone can't distinguish
    #    "scheduled" from "inline" here — only intercepting the
    #    scheduling call itself can.
    def test_post_schedules_runner_rather_than_calling_it_inline(self):
        with (
            patch.object(BackgroundTasks, "add_task") as mock_add_task,
            patch.object(api, "run_close_job") as mock_run_close_job,
        ):
            response = self.client.post(
                "/close-runs",
                data={"close_period": "2026-08"},
                files=_upload_files(
                    JOURNAL_FILE.read_bytes(), BANK_FILE.read_bytes()
                ),
            )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.job_ids.append(body["job_id"])

        mock_add_task.assert_called_once()
        scheduled_args = mock_add_task.call_args.args
        self.assertEqual(scheduled_args[0], api._run_job_in_background)
        self.assertEqual(scheduled_args[1], body["job_id"])
        mock_run_close_job.assert_not_called()

    # 3. A threading.Event-gated runner lets polling observe queued or
    #    running before the job reaches succeeded.
    def test_polling_observes_running_status_before_succeeded(self):
        gate = threading.Event()
        real_import_journal_entries = close_jobs.import_journal_entries

        def gated_import_journal_entries(*args, **kwargs):
            gate.wait(timeout=10)
            return real_import_journal_entries(*args, **kwargs)

        real_create_close_job = api.create_close_job
        created_job_ids: list[str] = []

        def spy_create_close_job(*args, **kwargs):
            job_id = real_create_close_job(*args, **kwargs)
            created_job_ids.append(job_id)
            return job_id

        with (
            patch.object(api, "create_close_job", side_effect=spy_create_close_job),
            patch.object(
                close_jobs,
                "import_journal_entries",
                side_effect=gated_import_journal_entries,
            ),
        ):
            thread = threading.Thread(
                target=lambda: self.client.post(
                    "/close-runs",
                    data={"close_period": "2026-08"},
                    files=_upload_files(
                        JOURNAL_FILE.read_bytes(), BANK_FILE.read_bytes()
                    ),
                )
            )
            thread.start()

            try:
                deadline = time.monotonic() + 10
                while not created_job_ids and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(created_job_ids, "job_id was not created in time")
                job_id = created_job_ids[0]
                self.job_ids.append(job_id)

                observed_running = False
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    status = self.client.get(f"/close-jobs/{job_id}").json()["status"]
                    if status == "running":
                        observed_running = True
                        break
                    time.sleep(0.01)
                self.assertTrue(
                    observed_running, "never observed status=running while gated"
                )
            finally:
                gate.set()
                thread.join(timeout=10)

        job = self._poll_job(job_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertIsNotNone(job["close_run_id"])

    # 8. Unknown job ID returns 404.
    def test_unknown_job_id_returns_404(self):
        response = self.client.get("/close-jobs/does-not-exist")
        self.assertEqual(response.status_code, 404)

    # 6. Multi-month files return the specified 422 period_ambiguous response.
    def test_multi_month_files_return_period_ambiguous(self):
        journal_csv = (
            JOURNAL_HEADER + "\n"
            "JE-1,2026-08-01,1000,cash,10.00,0.00,REF-1\n"
            "JE-2,2026-09-01,1000,cash,10.00,0.00,REF-2\n"
        ).encode()
        bank_csv = (BANK_HEADER + "\nBT-1,2026-08-01,deposit,10.00,REF-1\n").encode()

        response = self.client.post(
            "/close-runs",
            data={},
            files=_upload_files(journal_csv, bank_csv),
        )

        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["reason"], "period_ambiguous")
        self.assertEqual(detail["months"], ["2026-08", "2026-09"])

    # 7. Unparseable dates return the specified 422 unparseable_dates response.
    def test_unparseable_dates_return_structured_rows(self):
        journal_csv = (
            JOURNAL_HEADER + "\n" "JE-1,not-a-date,1000,cash,10.00,0.00,REF-1\n"
        ).encode()
        bank_csv = (BANK_HEADER + "\nBT-1,2026-08-01,deposit,10.00,REF-1\n").encode()

        response = self.client.post(
            "/close-runs",
            data={},
            files=_upload_files(journal_csv, bank_csv),
        )

        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["reason"], "unparseable_dates")
        self.assertEqual(len(detail["rows"]), 1)
        self.assertEqual(detail["rows"][0]["file"], "journal.csv")
        self.assertEqual(detail["rows"][0]["row_number"], 2)
        self.assertEqual(detail["rows"][0]["reason"], "unparseable")

    def test_empty_file_returns_plain_specific_reason(self):
        journal_csv = (JOURNAL_HEADER + "\n").encode()
        bank_csv = (BANK_HEADER + "\nBT-1,2026-08-01,deposit,10.00,REF-1\n").encode()

        response = self.client.post(
            "/close-runs",
            data={},
            files=_upload_files(journal_csv, bank_csv),
        )

        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["reason"], "empty_file")
        self.assertEqual(detail["file"], "journal.csv")

    def test_missing_date_column_returns_plain_specific_reason(self):
        journal_csv = (
            "journal_id,txn_date,account_code,description,debit,credit,"
            "external_reference\n"
            "JE-1,2026-08-01,1000,cash,10.00,0.00,REF-1\n"
        ).encode()
        bank_csv = (BANK_HEADER + "\nBT-1,2026-08-01,deposit,10.00,REF-1\n").encode()

        response = self.client.post(
            "/close-runs",
            data={},
            files=_upload_files(journal_csv, bank_csv),
        )

        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["reason"], "missing_date_column")
        self.assertEqual(detail["file"], "journal.csv")

    # 9. Startup recovery changes stale queued and running jobs to failed.
    def test_startup_recovery_marks_stale_queued_and_running_jobs_failed(self):
        queued_job_id = api.create_close_job(
            "2026-08", journal_source="a.csv", bank_source="b.csv"
        )
        running_job_id = api.create_close_job(
            "2026-08", journal_source="c.csv", bank_source="d.csv"
        )
        self.job_ids.extend([queued_job_id, running_job_id])

        running_job_dir = api._upload_dir(running_job_id)
        running_job_dir.mkdir(parents=True, exist_ok=True)
        (running_job_dir / api.JOURNAL_FILENAME).write_text("stub")

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE close_jobs SET status = 'running' WHERE job_id = %s",
                    (running_job_id,),
                )

        api._recover_on_startup()

        queued_job = api.get_close_job(queued_job_id)
        running_job = api.get_close_job(running_job_id)

        self.assertEqual(queued_job["status"], "failed")
        self.assertEqual(queued_job["error_detail"], api.STALE_JOB_ERROR_DETAIL)
        self.assertEqual(running_job["status"], "failed")
        self.assertEqual(running_job["error_detail"], api.STALE_JOB_ERROR_DETAIL)

        # error_detail must stay plain-language: no stack traces, paths,
        # credentials, or SQL leaking into a field a reviewer might see.
        self.assertNotIn("Traceback", running_job["error_detail"])
        self.assertNotIn(str(running_job_dir), running_job["error_detail"])
        self.assertNotIn("SELECT", running_job["error_detail"].upper())

        # 10. (startup half) abandoned upload directories are removed too.
        self.assertFalse(running_job_dir.exists())

    # 10. Upload directories are removed after both a successful and a
    #     failed background job.
    def test_upload_directory_removed_after_successful_job(self):
        response = self.client.post(
            "/close-runs",
            data={"close_period": "2026-08"},
            files=_upload_files(JOURNAL_FILE.read_bytes(), BANK_FILE.read_bytes()),
        )
        job_id = response.json()["job_id"]
        self.job_ids.append(job_id)

        self._poll_job(job_id)

        self.assertFalse(api._upload_dir(job_id).exists())

    def test_upload_directory_removed_after_failed_job(self):
        with patch.object(api, "run_close_job", side_effect=RuntimeError("boom")):
            response = self.client.post(
                "/close-runs",
                data={"close_period": "2026-08"},
                files=_upload_files(
                    JOURNAL_FILE.read_bytes(), BANK_FILE.read_bytes()
                ),
            )

        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        self.job_ids.append(job_id)

        self.assertFalse(api._upload_dir(job_id).exists())


if __name__ == "__main__":
    unittest.main()
