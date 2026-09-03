import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.api import app


class ApiTest(unittest.TestCase):
    def test_health_endpoint_returns_ok(self):
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_exceptions_endpoint_returns_open_exceptions(self):
        client = TestClient(app)

        response = client.get("/exceptions")

        self.assertEqual(response.status_code, 200)
        exceptions = response.json()
        self.assertIsInstance(exceptions, list)

        for exception in exceptions:
            self.assertEqual(exception["status"], "open")
            self.assertIn("exception_id", exception)
            self.assertIn("exception_type", exception)
            self.assertIn("severity", exception)
            self.assertIn("source_ids", exception)


if __name__ == "__main__":
    unittest.main()