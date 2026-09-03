from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .accounting import JournalLine


@dataclass(frozen=True)
class BankTransaction:
    transaction_id: str
    date: str
    description: str
    amount: Decimal
    external_reference: str


def load_bank_transactions(path: str | Path) -> list[BankTransaction]:
    with open(path, newline="", encoding="utf-8") as file:
        return [
            BankTransaction(
                transaction_id=row["transaction_id"],
                date=row["date"],
                description=row["description"],
                amount=Decimal(row["amount"]),
                external_reference=row["external_reference"],
            )
            for row in csv.DictReader(file)
        ]


def cash_amount(line: JournalLine) -> Decimal:
    return line.debit - line.credit


def reconcile(lines: list[JournalLine], bank_transactions: list[BankTransaction]) -> list[dict[str, str]]:
    cash_lines = [line for line in lines if line.account_code == "1000"]
    unmatched = []
    for transaction in bank_transactions:
        matches = [
            line
            for line in cash_lines
            if line.external_reference == transaction.external_reference
            and cash_amount(line) == transaction.amount
        ]
        if len(matches) != 1:
            reason = "No matching cash ledger line" if not matches else "Multiple matching cash ledger lines"
            unmatched.append(
                {
                    "bank_transaction_id": transaction.transaction_id,
                    "reason": reason,
                    "amount": f"{transaction.amount:.2f}",
                    "external_reference": transaction.external_reference,
                }
            )
    return unmatched
