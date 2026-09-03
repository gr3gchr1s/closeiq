from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .database import get_connection


def import_bank_transactions(path: str | Path) -> int:
    with open(path, newline="", encoding="utf-8") as file:
        transactions = list(csv.DictReader(file))

    with get_connection() as connection:
        with connection.cursor() as cursor:
            for transaction in transactions:
                cursor.execute(
                    """
                    INSERT INTO bank_transactions (
                        bank_transaction_id,
                        transaction_date,
                        description,
                        amount,
                        external_reference
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (bank_transaction_id)
                    DO UPDATE SET
                        transaction_date = EXCLUDED.transaction_date,
                        description = EXCLUDED.description,
                        amount = EXCLUDED.amount,
                        external_reference = EXCLUDED.external_reference
                    """,
                    (
                        transaction["transaction_id"],
                        transaction["date"],
                        transaction["description"],
                        Decimal(transaction["amount"]),
                        transaction["external_reference"],
                    ),
                )

    return len(transactions)