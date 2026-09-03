from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class JournalLine:
    journal_id: str
    date: str
    account_code: str
    description: str
    debit: Decimal
    credit: Decimal
    external_reference: str


def load_journal_lines(path: str | Path) -> list[JournalLine]:
    with open(path, newline="", encoding="utf-8") as file:
        return [
            JournalLine(
                journal_id=row["journal_id"],
                date=row["date"],
                account_code=row["account_code"],
                description=row["description"],
                debit=Decimal(row["debit"]),
                credit=Decimal(row["credit"]),
                external_reference=row["external_reference"],
            )
            for row in csv.DictReader(file)
        ]

def find_duplicate_external_references(lines: list[JournalLine]) -> list[dict[str, str]]:
    reference_to_journals: dict[str, set[str]] = defaultdict(set)

    for line in lines:
        if line.external_reference:
            reference_to_journals[line.external_reference].add(line.journal_id)

    exceptions = []
    for external_reference, journal_ids in reference_to_journals.items():
        if len(journal_ids) > 1:
            exceptions.append(
                {
                    "external_reference": external_reference,
                    "reason": "External reference appears in multiple journal entries",
                    "journal_ids": ", ".join(sorted(journal_ids)),
                }
            )

    return exceptions

def validate_journal_balance(lines: list[JournalLine]) -> list[dict[str, str]]:
    totals: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"debit": Decimal("0"), "credit": Decimal("0")}
    )
    for line in lines:
        totals[line.journal_id]["debit"] += line.debit
        totals[line.journal_id]["credit"] += line.credit

    exceptions = []
    for journal_id, total in totals.items():
        difference = total["debit"] - total["credit"]
        if difference:
            exceptions.append(
                {
                    "journal_id": journal_id,
                    "reason": "Journal entry is not balanced",
                    "difference": f"{difference:.2f}",
                }
            )
    return exceptions