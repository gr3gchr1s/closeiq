import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.api import app
from closeiq.database import get_connection


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.test_exception_id = "api-test:decision-workflow"
        self.test_close_run_id = "api-test:close-run-history"

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
                        journal_source,
                        bank_source,
                        imported_journal_line_count,
                        imported_bank_transaction_count,
                        total_exception_count
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (close_run_id) DO UPDATE
                    SET
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
                        "test-journal.csv",
                        "test-bank.csv",
                        9,
                        4,
                        4,
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

        self.assertEqual(close_run["journal_source"], "test-journal.csv")
        self.assertEqual(close_run["bank_source"], "test-bank.csv")
        self.assertEqual(close_run["imported_journal_line_count"], 9)
        self.assertEqual(close_run["imported_bank_transaction_count"], 4)
        self.assertEqual(close_run["total_exception_count"], 4)
        self.assertIn("created_at", close_run)

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