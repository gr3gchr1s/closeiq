from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .database import get_connection


def import_journal_entries(path: str | Path) -> int:
    with open(path, newline="", encoding="utf-8") as file:
        journal_lines = list(csv.DictReader(file))

    lines_by_journal: dict[str, list[dict[str, str]]] = defaultdict(list)

    for line in journal_lines:
        lines_by_journal[line["journal_id"]].append(line)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            for journal_id, lines in lines_by_journal.items():
                first_line = lines[0]

                cursor.execute(
                    """
                    INSERT INTO journal_entries (
                        journal_id,
                        journal_date,
                        description
                    )
                    VALUES (%s, %s, %s)
                    ON CONFLICT (journal_id)
                    DO UPDATE SET
                        journal_date = EXCLUDED.journal_date,
                        description = EXCLUDED.description
                    """,
                    (
                        journal_id,
                        first_line["date"],
                        first_line["description"],
                    ),
                )

                for line_number, line in enumerate(lines, start=1):
                    cursor.execute(
                        """
                        INSERT INTO journal_lines (
                            journal_id,
                            line_number,
                            account_code,
                            description,
                            debit,
                            credit,
                            external_reference
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (journal_id, line_number)
                        DO UPDATE SET
                            account_code = EXCLUDED.account_code,
                            description = EXCLUDED.description,
                            debit = EXCLUDED.debit,
                            credit = EXCLUDED.credit,
                            external_reference = EXCLUDED.external_reference
                        """,
                        (
                            journal_id,
                            line_number,
                            line["account_code"],
                            line["description"],
                            Decimal(line["debit"]),
                            Decimal(line["credit"]),
                            line["external_reference"],
                        ),
                    )

    return len(journal_lines)