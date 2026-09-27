import sys
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.api import app
from closeiq.database import get_connection


PROJECT_ROOT = Path(__file__).parents[1]
JOURNAL_FILE = PROJECT_ROOT / "data" / "journal_entries.csv"
BANK_FILE = PROJECT_ROOT / "data" / "bank_transactions.csv"

JOURNAL_HEADER = (
    "journal_id,date,account_code,description,debit,credit,external_reference"
)
BANK_HEADER = "transaction_id,date,description,amount,external_reference"


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


class FrontendIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.job_ids: list[str] = []

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

    # 1. GET / returns 200 and contains the CloseIQ upload page.
    def test_root_serves_upload_page(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("CloseIQ", response.text)
        self.assertIn("Run Close Review", response.text)
        self.assertIn('data-page="upload"', response.text)

    # 2. Static assets are served.
    def test_static_assets_are_served(self):
        css_response = self.client.get("/static/styles.css")
        js_response = self.client.get("/static/app.js")

        self.assertEqual(css_response.status_code, 200)
        self.assertIn("css", css_response.headers["content-type"])

        self.assertEqual(js_response.status_code, 200)
        self.assertIn(
            js_response.headers["content-type"],
            ("text/javascript; charset=utf-8", "application/javascript"),
        )

    # 3. GET /jobs/{job_id} returns 200 and contains the job-results shell,
    # for an id that was never created.
    def test_jobs_route_serves_shell_for_any_job_id(self):
        response = self.client.get("/jobs/example-job-id")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("CloseIQ", response.text)
        self.assertIn('data-page="jobs"', response.text)

    # 4. Existing API routes remain reachable after static integration.
    def test_existing_api_routes_remain_reachable(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/close-runs").status_code, 200)
        self.assertEqual(
            self.client.get("/close-jobs/does-not-exist").status_code, 404
        )
        self.assertEqual(
            self.client.get("/close-runs/does-not-exist/exceptions").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                "/close-runs/does-not-exist/close-summary"
            ).status_code,
            404,
        )
        # A malformed POST (no files) still reaches the real handler and
        # gets FastAPI's own validation error, not a static 404.
        self.assertEqual(self.client.post("/close-runs").status_code, 422)

    # 5. Upload valid single-month files through the browser API contract.
    def test_full_upload_flow_reaches_succeeded_with_results(self):
        with (
            JOURNAL_FILE.open("rb") as journal_file,
            BANK_FILE.open("rb") as bank_file,
        ):
            response = self.client.post(
                "/close-runs",
                data={},
                files={
                    "journal_file": (
                        "journal_entries.csv",
                        journal_file,
                        "text/csv",
                    ),
                    "bank_file": (
                        "bank_transactions.csv",
                        bank_file,
                        "text/csv",
                    ),
                },
            )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(set(body), {"job_id", "status"})
        self.assertEqual(body["status"], "queued")
        job_id = body["job_id"]
        self.job_ids.append(job_id)

        job = self._poll_job(job_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertIsNotNone(job["close_run_id"])

        summary_response = self.client.get(
            f"/close-runs/{job['close_run_id']}/close-summary"
        )
        exceptions_response = self.client.get(
            f"/close-runs/{job['close_run_id']}/exceptions"
        )

        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(exceptions_response.status_code, 200)
        self.assertEqual(summary_response.json()["total"], 4)
        self.assertEqual(len(exceptions_response.json()), 4)

    # 6. A multi-month upload returns the documented ambiguity response.
    def test_multi_month_upload_returns_documented_ambiguity_response(self):
        journal_csv = (
            JOURNAL_HEADER + "\n"
            "JE-1,2026-08-01,1000,cash,10.00,0.00,REF-1\n"
            "JE-2,2026-09-01,1000,cash,10.00,0.00,REF-2\n"
        ).encode()
        bank_csv = (BANK_HEADER + "\nBT-1,2026-08-01,deposit,10.00,REF-1\n").encode()

        response = self.client.post(
            "/close-runs",
            data={},
            files={
                "journal_file": ("journal.csv", journal_csv, "text/csv"),
                "bank_file": ("bank.csv", bank_csv, "text/csv"),
            },
        )

        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["reason"], "period_ambiguous")
        self.assertEqual(detail["months"], ["2026-08", "2026-09"])

    # 7. An unknown job API request returns 404; the job shell route
    # itself still loads successfully.
    def test_unknown_job_api_404_but_shell_route_still_loads(self):
        api_response = self.client.get("/close-jobs/does-not-exist")
        shell_response = self.client.get("/jobs/does-not-exist")

        self.assertEqual(api_response.status_code, 404)
        self.assertEqual(shell_response.status_code, 200)
        self.assertIn('data-page="jobs"', shell_response.text)

    # 8. No static path traversal is possible through the exposed mount.
    def test_static_mount_rejects_path_traversal(self):
        traversal_paths = [
            "/static/%2e%2e/pyproject.toml",
            "/static/%2e%2e/%2e%2e/Dockerfile",
            "/static/..%2fpyproject.toml",
            "/static/%2e%2e%2fsrc%2fcloseiq%2fapi.py",
        ]

        for path in traversal_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertNotEqual(
                    response.status_code,
                    200,
                    f"{path} unexpectedly returned 200",
                )


if __name__ == "__main__":
    unittest.main()
