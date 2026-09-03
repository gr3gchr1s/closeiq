import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.database import get_connection


class DatabaseConnectionTest(unittest.TestCase):
    def test_database_connection_runs_a_query(self):
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()

        self.assertEqual(result[0], 1)


if __name__ == "__main__":
    unittest.main()