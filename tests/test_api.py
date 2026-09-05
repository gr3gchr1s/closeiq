import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.api import app
from closeiq.database import get_connection


PROJECT_ROOT = Path(__file__).parents[1]


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.test_exception_id = "api-test:decision-workflow"
        self.test_close_run_id = "api-test:close-run-history"
        self.test_close_run_exception_id = "api-test:run-snapshot"

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO close_exceptions (
                        exception_id,
                        exception_type,
                        severity,
                        status,
                        source_ids,
                        evidence
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (exception_id) DO UPDATE
                    SET
                        exception_type = EXCLUDED.exception_type,
                        severity = EXCLUDED.severity,
                        status = EXCLUDED.status,
                        source_ids = EXCLUDED.source_ids,
                        evidence = EXCLUDED.evidence
                    """,
                    (
                        self.test_exception_id,
                        "test_exception",
                        "low",
                        "open",
                        ["api-test-source"],
                        "{}",
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO close_runs (
                        close_run_id,
                        close_period,
                        journal_source,
                        bank_source,
                        imported_journal_line_count,
                        imported_bank_transaction_count,
                        total_exception_count
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (close_run_id) DO UPDATE
                    SET
                        close_period = EXCLUDED.close_period,
                        journal_source = EXCLUDED.journal_source,
                        bank_source = EXCLUDED.bank_source,
                        imported_journal_line_count =
                            EXCLUDED.imported_journal_line_count,
                        imported_bank_transaction_count =
                            EXCLUDED.imported_bank_transaction_count,
                        total_exception_count =
                            EXCLUDED.total_exception_count
                    """,
                    (
                        self.test_close_run_id,
                        "2026-08",
                        "test-journal.csv",
                        "test-bank.csv",
                        9,
                        4,
                        4,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO close_run_exceptions (
                        close_run_id,
                        exception_id,
                        exception_type,
                        severity,
                        status,
                        source_ids,
                        evidence
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (close_run_id, exception_id) DO UPDATE
                    SET
                        exception_type = EXCLUDED.exception_type,
                        severity = EXCLUDED.severity,
                        status = EXCLUDED.status,
                        source_ids = EXCLUDED.source_ids,
                        evidence = EXCLUDED.evidence
                    """,
                    (
                        self.test_close_run_id,
                        self.test_close_run_exception_id,
                        "journal_balance",
                        "high",
                        "open",
                        ["JE-1004"],
                        '{"reason": "Journal entry is not balanced"}',
                    ),
                )

    def tearDown(self):
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM exception_decisions
                    WHERE exception_id = %s
                    """,
                    (self.test_exception_id,),
                )
                cursor.execute(
                    """
                    DELETE FROM close_run_exceptions
                    WHERE close_run_id = %s
                    """,
                    (self.test_close_run_id,),
                )
                cursor.execute(
                    """
                    DELETE FROM close_exceptions
                    WHERE exception_id = %s
                    """,
                    (self.test_exception_id,),
                )
                cursor.execute(
                    """
                    DELETE FROM close_runs
                    WHERE close_run_id = %s
                    """,
                    (self.test_close_run_id,),
                )

    def test_health_endpoint_returns_ok(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_exceptions_endpoint_returns_open_exceptions(self):
        response = self.client.get("/exceptions")

        self.assertEqual(response.status_code, 200)
        exceptions = response.json()
        self.assertIsInstance(exceptions, list)

        for exception in exceptions:
            self.assertEqual(exception["status"], "open")
            self.assertIn("exception_id", exception)
            self.assertIn("exception_type", exception)
            self.assertIn("severity", exception)
            self.assertIn("source_ids", exception)

    def test_close_runs_endpoint_returns_history(self):
        response = self.client.get("/close-runs")

        self.assertEqual(response.status_code, 200)
        close_runs = response.json()

        self.assertIsInstance(close_runs, list)
        close_run = next(
            run
            for run in close_runs
            if run["close_run_id"] == self.test_close_run_id
        )

        self.assertEqual(close_run["close_period"], "2026-08")
        self.assertEqual(close_run["journal_source"], "test-journal.csv")
        self.assertEqual(close_run["bank_source"], "test-bank.csv")
        self.assertEqual(close_run["imported_journal_line_count"], 9)
        self.assertEqual(close_run["imported_bank_transaction_count"], 4)
        self.assertEqual(close_run["total_exception_count"], 4)
        self.assertIn("created_at", close_run)

    def test_close_run_exceptions_endpoint_returns_snapshots(self):
        response = self.client.get(
            f"/close-runs/{self.test_close_run_id}/exceptions"
        )

        self.assertEqual(response.status_code, 200)
        snapshots = response.json()

        self.assertEqual(len(snapshots), 1)

        snapshot = snapshots[0]
        self.assertEqual(
            snapshot["exception_id"],
            self.test_close_run_exception_id,
        )
        self.assertEqual(snapshot["exception_type"], "journal_balance")
        self.assertEqual(snapshot["severity"], "high")
        self.assertEqual(snapshot["status"], "open")
        self.assertEqual(snapshot["source_ids"], ["JE-1004"])
        self.assertEqual(
            snapshot["evidence"]["reason"],
            "Journal entry is not balanced",
        )

    def test_create_close_run_endpoint_imports_uploaded_csv_files(self):
        journal_path = PROJECT_ROOT / "data" / "journal_entries.csv"
        bank_path = PROJECT_ROOT / "data" / "bank_transactions.csv"

        with (
            journal_path.open("rb") as journal_file,
            bank_path.open("rb") as bank_file,
        ):
            response = self.client.post(
                "/close-runs",
                data={"close_period": "2026-08"},
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

        self.assertEqual(response.status_code, 201)
        result = response.json()
        close_run_id = result["close_run_id"]

        try:
            self.assertEqual(result["close_period"], "2026-08")
            self.assertEqual(result["journal_source"], "journal_entries.csv")
            self.assertEqual(result["bank_source"], "bank_transactions.csv")
            self.assertEqual(result["imported_journal_line_count"], 9)
            self.assertEqual(
                result["imported_bank_transaction_count"],
                4,
            )
            self.assertEqual(
                result["close_review"]["summary"]["total_exception_count"],
                4,
            )
        finally:
            with get_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        DELETE FROM close_run_exceptions
                        WHERE close_run_id = %s
                        """,
                        (close_run_id,),
                    )
                    cursor.execute(
                        """
                        DELETE FROM close_runs
                        WHERE close_run_id = %s
                        """,
                        (close_run_id,),
                    )

    def test_decision_endpoint_acknowledges_exception(self):
        response = self.client.post(
            f"/exceptions/{self.test_exception_id}/decisions",
            json={
                "decision": "acknowledge",
                "note": "Reviewed the supporting accounting evidence.",
            },
        )

        self.assertEqual(response.status_code, 201)
        result = response.json()
        self.assertEqual(result["exception_id"], self.test_exception_id)
        self.assertEqual(result["decision"], "acknowledge")
        self.assertEqual(result["status"], "reviewed")
        self.assertEqual(
            result["note"],
            "Reviewed the supporting accounting evidence.",
        )

    def test_close_summary_endpoint_returns_status_counts(self):
        response = self.client.get("/close-summary")

        self.assertEqual(response.status_code, 200)
        summary = response.json()

        self.assertEqual(
            set(summary),
            {"open", "reviewed", "resolved", "dismissed", "total"},
        )

        for status in ("open", "reviewed", "resolved", "dismissed"):
            self.assertIsInstance(summary[status], int)

        self.assertEqual(
            summary["total"],
            summary["open"]
            + summary["reviewed"]
            + summary["resolved"]
            + summary["dismissed"],
        )

    def test_decision_history_endpoint_returns_audit_trail(self):
        create_response = self.client.post(
            f"/exceptions/{self.test_exception_id}/decisions",
            json={
                "decision": "acknowledge",
                "note": "Reviewed the supporting accounting evidence.",
            },
        )
        self.assertEqual(create_response.status_code, 201)

        response = self.client.get(
            f"/exceptions/{self.test_exception_id}/decisions"
        )

        self.assertEqual(response.status_code, 200)
        decisions = response.json()
        self.assertEqual(len(decisions), 1)

        decision = decisions[0]
        self.assertEqual(decision["exception_id"], self.test_exception_id)
        self.assertEqual(decision["decision"], "acknowledge")
        self.assertEqual(decision["reviewer"], "local-user")
        self.assertEqual(
            decision["note"],
            "Reviewed the supporting accounting evidence.",
        )


if __name__ == "__main__":
    unittest.main()