import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.accounting import (
    JournalLine,
    find_duplicate_external_references,
    validate_journal_balance,
)
from closeiq.reconciliation import BankTransaction, reconcile
from decimal import Decimal


class AccountingControlsTest(unittest.TestCase):
    def test_unbalanced_journal_is_an_exception(self):
        lines = [
            JournalLine("JE-1", "2026-08-01", "1000", "cash", Decimal("10"), Decimal("0"), "REF-1"),
            JournalLine("JE-1", "2026-08-01", "4000", "revenue", Decimal("0"), Decimal("8"), "REF-1"),
        ]
        self.assertEqual(validate_journal_balance(lines)[0]["difference"], "2.00")

    def test_unmatched_bank_transaction_is_not_silently_matched(self):
        lines = [JournalLine("JE-1", "2026-08-01", "1000", "cash", Decimal("100"), Decimal("0"), "DEP-1")]
        bank = [BankTransaction("BT-1", "2026-08-01", "deposit", Decimal("99"), "DEP-1")]
        self.assertEqual(reconcile(lines, bank)[0]["reason"], "No matching cash ledger line")

    def test_duplicate_reference_across_journal_entries_is_an_exception(self):
        lines = [
            JournalLine(
                "JE-1", "2026-08-01", "1000", "cash",
                Decimal("85"), Decimal("0"), "ACH-8102"
            ),
            JournalLine(
                "JE-2", "2026-08-02", "1000", "cash",
                Decimal("85"), Decimal("0"), "ACH-8102"
            ),
        ]

        exceptions = find_duplicate_external_references(lines)

        self.assertEqual(len(exceptions), 1)
        self.assertEqual(exceptions[0]["external_reference"], "ACH-8102")
        
if __name__ == "__main__":
    unittest.main()
