from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import psycopg

from .database import get_connection


def _insert_bank_transactions(
    cursor: psycopg.Cursor,
    import_batch_id: str,
    transactions: list[dict[str, str]],
) -> None:
    for transaction in transactions:
        # Plain INSERT, not an upsert: uniqueness is scoped to
        # (import_batch_id, bank_transaction_id) (see
        # database/schema.sql), and import_batch_id is always a fresh
        # close_jobs.job_id, so no legitimate conflict can occur within
        # one batch.
        cursor.execute(
            """
            INSERT INTO bank_transactions (
                import_batch_id,
                bank_transaction_id,
                transaction_date,
                description,
                amount,
                external_reference
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                import_batch_id,
                transaction["transaction_id"],
                transaction["date"],
                transaction["description"],
                Decimal(transaction["amount"]),
                transaction["external_reference"],
            ),
        )


def import_bank_transactions(
    path: str | Path,
    *,
    import_batch_id: str,
    connection: psycopg.Connection | None = None,
) -> int:
    """Import bank transactions, scoped to one import batch.

    See ``import_journal_entries`` for the ``import_batch_id`` and
    ``connection`` contract this mirrors.
    """
    with open(path, newline="", encoding="utf-8") as file:
        transactions = list(csv.DictReader(file))

    if connection is not None:
        with connection.cursor() as cursor:
            _insert_bank_transactions(cursor, import_batch_id, transactions)
    else:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                _insert_bank_transactions(
                    cursor, import_batch_id, transactions
                )

    return len(transactions)