from __future__ import annotations

import csv
from pathlib import Path

from .database import get_connection


def seed_accounts(path: str | Path) -> int:
    with open(path, newline="", encoding="utf-8") as file:
        accounts = list(csv.DictReader(file))

    with get_connection() as connection:
        with connection.cursor() as cursor:
            for account in accounts:
                cursor.execute(
                    """
                    INSERT INTO accounts (
                        account_code,
                        account_name,
                        account_type,
                        normal_balance
                    )
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (account_code)
                    DO UPDATE SET
                        account_name = EXCLUDED.account_name,
                        account_type = EXCLUDED.account_type,
                        normal_balance = EXCLUDED.normal_balance
                    """,
                    (
                        account["account_code"],
                        account["account_name"],
                        account["account_type"],
                        account["normal_balance"],
                    ),
                )

    return len(accounts)