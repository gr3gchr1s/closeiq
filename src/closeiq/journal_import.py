from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import psycopg

from .database import get_connection


def _insert_journal_entries(
    cursor: psycopg.Cursor,
    import_batch_id: str,
    lines_by_journal: dict[str, list[dict[str, str]]],
) -> None:
    for journal_id, lines in lines_by_journal.items():
        first_line = lines[0]

        # Plain INSERT, not an upsert: uniqueness is scoped to
        # (import_batch_id, journal_id) (see database/schema.sql), and
        # import_batch_id is always a fresh close_jobs.job_id, so no
        # legitimate conflict can occur within one batch. A conflict here
        # indicates a bug (e.g. a reused batch id), not a re-import to
        # tolerate.
        cursor.execute(
            """
            INSERT INTO journal_entries (
                import_batch_id,
                journal_id,
                journal_date,
                description
            )
            VALUES (%s, %s, %s, %s)
            """,
            (
                import_batch_id,
                journal_id,
                first_line["date"],
                first_line["description"],
            ),
        )

        for line_number, line in enumerate(lines, start=1):
            cursor.execute(
                """
                INSERT INTO journal_lines (
                    import_batch_id,
                    journal_id,
                    line_number,
                    account_code,
                    description,
                    debit,
                    credit,
                    external_reference
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    import_batch_id,
                    journal_id,
                    line_number,
                    line["account_code"],
                    line["description"],
                    Decimal(line["debit"]),
                    Decimal(line["credit"]),
                    line["external_reference"],
                ),
            )


def import_journal_entries(
    path: str | Path,
    *,
    import_batch_id: str,
    connection: psycopg.Connection | None = None,
) -> int:
    """Import journal entries and lines, scoped to one import batch.

    ``import_batch_id`` must be an existing ``close_jobs.job_id`` (see
    docs/closeiq_v2_architecture.md, section 3) — every imported row is
    tagged with it so a later batch that reuses a journal_id cannot
    overwrite this one.

    If ``connection`` is given, the insert runs on it without committing
    or closing it, so it can participate in a caller-managed transaction
    (see close_jobs.run_close_job). Otherwise a dedicated connection is
    opened and committed on success.
    """
    with open(path, newline="", encoding="utf-8") as file:
        journal_lines = list(csv.DictReader(file))

    lines_by_journal: dict[str, list[dict[str, str]]] = defaultdict(list)

    for line in journal_lines:
        lines_by_journal[line["journal_id"]].append(line)

    if connection is not None:
        with connection.cursor() as cursor:
            _insert_journal_entries(cursor, import_batch_id, lines_by_journal)
    else:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                _insert_journal_entries(
                    cursor, import_batch_id, lines_by_journal
                )

    return len(journal_lines)