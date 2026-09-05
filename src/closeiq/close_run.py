from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from .accounting import load_journal_lines
from .bank_import import import_bank_transactions
from .close_review import build_close_review
from .database import get_connection
from .exception_store import upsert_close_exceptions
from .journal_import import import_journal_entries
from .reconciliation import load_bank_transactions


def run_close(
    journal_file: str | Path,
    bank_file: str | Path,
) -> dict[str, Any]:
    imported_journal_line_count = import_journal_entries(journal_file)
    imported_bank_transaction_count = import_bank_transactions(bank_file)

    journal_lines = load_journal_lines(journal_file)
    bank_transactions = load_bank_transactions(bank_file)

    close_review = build_close_review(journal_lines, bank_transactions)
    upsert_close_exceptions(close_review["workflow_exceptions"])

    close_run_id = str(uuid4())
    total_exception_count = close_review["summary"]["total_exception_count"]

    with get_connection() as connection:
        with connection.cursor() as cursor:
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
                """,
                (
                    close_run_id,
                    str(journal_file),
                    str(bank_file),
                    imported_journal_line_count,
                    imported_bank_transaction_count,
                    total_exception_count,
                ),
            )

    return {
        "close_run_id": close_run_id,
        "imported_journal_line_count": imported_journal_line_count,
        "imported_bank_transaction_count": imported_bank_transaction_count,
        "close_review": close_review,
    }