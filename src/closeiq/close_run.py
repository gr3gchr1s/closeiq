from __future__ import annotations

import json
import re
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


CLOSE_PERIOD_PATTERN = r"\d{4}-(0[1-9]|1[0-2])"


def run_close(
    journal_file: str | Path,
    bank_file: str | Path,
    *,
    close_period: str,
    journal_source: str | None = None,
    bank_source: str | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(CLOSE_PERIOD_PATTERN, close_period):
        raise ValueError(
            "close_period must use YYYY-MM format, such as 2026-08"
        )

    imported_journal_line_count = import_journal_entries(journal_file)
    imported_bank_transaction_count = import_bank_transactions(bank_file)

    journal_lines = load_journal_lines(journal_file)
    bank_transactions = load_bank_transactions(bank_file)

    close_review = build_close_review(journal_lines, bank_transactions)
    workflow_exceptions = close_review["workflow_exceptions"]

    upsert_close_exceptions(workflow_exceptions)

    close_run_id = str(uuid4())

    with get_connection() as connection:
        with connection.cursor() as cursor:
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
                """,
                (
                    close_run_id,
                    close_period,
                    journal_source or str(journal_file),
                    bank_source or str(bank_file),
                    imported_journal_line_count,
                    imported_bank_transaction_count,
                    close_review["summary"]["total_exception_count"],
                ),
            )

            cursor.executemany(
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
                """,
                [
                    (
                        close_run_id,
                        exception["exception_id"],
                        exception["exception_type"],
                        exception["severity"],
                        exception["status"],
                        exception["source_ids"],
                        json.dumps(
                            {
                                key: value
                                for key, value in exception.items()
                                if key
                                not in {
                                    "exception_id",
                                    "exception_type",
                                    "severity",
                                    "status",
                                    "source_ids",
                                }
                            },
                            default=str,
                        ),
                    )
                    for exception in workflow_exceptions
                ],
            )

    return {
        "close_run_id": close_run_id,
        "close_period": close_period,
        "journal_source": journal_source or str(journal_file),
        "bank_source": bank_source or str(bank_file),
        "imported_journal_line_count": imported_journal_line_count,
        "imported_bank_transaction_count": imported_bank_transaction_count,
        "close_review": close_review,
    }