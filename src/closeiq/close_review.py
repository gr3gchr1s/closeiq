from __future__ import annotations

from .accounting import (
    JournalLine,
    find_duplicate_external_references,
    validate_journal_balance,
)
from .reconciliation import BankTransaction, reconcile


def build_workflow_exception(
    exception_id: str,
    exception_type: str,
    severity: str,
    source_ids: list[str],
    details: dict[str, str],
) -> dict[str, object]:
    return {
        "exception_id": exception_id,
        "exception_type": exception_type,
        "severity": severity,
        "status": "open",
        "source_ids": source_ids,
        **details,
    }


def build_close_review(
    lines: list[JournalLine],
    bank_transactions: list[BankTransaction],
) -> dict[str, object]:
    unbalanced_journals = validate_journal_balance(lines)
    duplicate_references = find_duplicate_external_references(lines)
    bank_reconciliation_exceptions = reconcile(lines, bank_transactions)

    workflow_exceptions = []

    for exception in unbalanced_journals:
        journal_id = exception["journal_id"]
        workflow_exceptions.append(
            build_workflow_exception(
                exception_id=f"journal-balance:{journal_id}",
                exception_type="journal_balance",
                severity="high",
                source_ids=[journal_id],
                details=exception,
            )
        )

    for exception in duplicate_references:
        external_reference = exception["external_reference"]
        journal_ids = [
            journal_id.strip()
            for journal_id in exception["journal_ids"].split(",")
        ]
        workflow_exceptions.append(
            build_workflow_exception(
                exception_id=f"duplicate-reference:{external_reference}",
                exception_type="duplicate_reference",
                severity="medium",
                source_ids=journal_ids,
                details=exception,
            )
        )

    for exception in bank_reconciliation_exceptions:
        bank_transaction_id = exception["bank_transaction_id"]
        workflow_exceptions.append(
            build_workflow_exception(
                exception_id=f"bank-reconciliation:{bank_transaction_id}",
                exception_type="bank_reconciliation",
                severity="medium",
                source_ids=[bank_transaction_id],
                details=exception,
            )
        )

    total_exception_count = len(workflow_exceptions)

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
        "workflow_exceptions": workflow_exceptions,
    }