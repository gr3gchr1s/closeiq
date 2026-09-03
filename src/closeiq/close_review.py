from __future__ import annotations

from .accounting import (
    JournalLine,
    find_duplicate_external_references,
    validate_journal_balance,
)
from .reconciliation import BankTransaction, reconcile


def build_close_review(
    lines: list[JournalLine],
    bank_transactions: list[BankTransaction],
) -> dict[str, object]:
    unbalanced_journals = validate_journal_balance(lines)
    duplicate_references = find_duplicate_external_references(lines)
    bank_reconciliation_exceptions = reconcile(lines, bank_transactions)

    total_exception_count = (
        len(unbalanced_journals)
        + len(duplicate_references)
        + len(bank_reconciliation_exceptions)
    )

    return {
        "summary": {
            "unbalanced_journal_count": len(unbalanced_journals),
            "duplicate_reference_count": len(duplicate_references),
            "bank_reconciliation_exception_count": len(
                bank_reconciliation_exceptions
            ),
            "total_exception_count": total_exception_count,
        },
        "exceptions": {
            "unbalanced_journals": unbalanced_journals,
            "duplicate_references": duplicate_references,
            "bank_reconciliation": bank_reconciliation_exceptions,
        },
    }