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


if __name__ == "__main__":
    unittest.main()