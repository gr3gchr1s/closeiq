import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from closeiq.period_inference import (
    DATE_COLUMN,
    EmptyFileError,
    MissingDateColumnError,
    PeriodAmbiguousError,
    UnparseableDatesError,
    infer_close_period,
)


def _write_csv(directory: Path, filename: str, header: str, rows: list[str]) -> Path:
    path = directory / filename
    path.write_text("\n".join([header, *rows]) + ("\n" if rows else ""), encoding="utf-8")
    return path


JOURNAL_HEADER = "journal_id,date,account_code,description,debit,credit,external_reference"
BANK_HEADER = "transaction_id,date,description,amount,external_reference"


class PeriodInferenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)

    def _journal(self, rows: list[str]) -> Path:
        return _write_csv(self.directory, "journal.csv", JOURNAL_HEADER, rows)

    def _bank(self, rows: list[str]) -> Path:
        return _write_csv(self.directory, "bank.csv", BANK_HEADER, rows)

    def test_single_month_across_both_files_returns_that_period(self):
        journal = self._journal(
            [
                "JE-1,2026-08-02,1000,cash,10.00,0.00,REF-1",
                "JE-1,2026-08-02,4000,revenue,0.00,10.00,REF-1",
            ]
        )
        bank = self._bank(["BT-1,2026-08-05,deposit,10.00,REF-1"])

        self.assertEqual(infer_close_period(journal, bank), "2026-08")

    def test_multiple_months_within_one_file_raises_ambiguous(self):
        journal = self._journal(
            [
                "JE-1,2026-08-02,1000,cash,10.00,0.00,REF-1",
                "JE-2,2026-09-02,1000,cash,10.00,0.00,REF-2",
            ]
        )
        bank = self._bank(["BT-1,2026-08-05,deposit,10.00,REF-1"])

        with self.assertRaises(PeriodAmbiguousError) as context:
            infer_close_period(journal, bank)

        self.assertEqual(context.exception.months, ["2026-08", "2026-09"])

    def test_months_split_across_journal_and_bank_files_raises_ambiguous(self):
        # Each file individually spans only one month, but the two
        # months differ between the files.
        journal = self._journal(["JE-1,2026-08-02,1000,cash,10.00,0.00,REF-1"])
        bank = self._bank(["BT-1,2026-09-05,deposit,10.00,REF-1"])

        with self.assertRaises(PeriodAmbiguousError) as context:
            infer_close_period(journal, bank)

        self.assertEqual(context.exception.months, ["2026-08", "2026-09"])

    def test_invalid_date_raises_unparseable_with_row_detail(self):
        journal = self._journal(
            ["JE-1,not-a-date,1000,cash,10.00,0.00,REF-1"]
        )
        bank = self._bank(["BT-1,2026-08-05,deposit,10.00,REF-1"])

        with self.assertRaises(UnparseableDatesError) as context:
            infer_close_period(journal, bank)

        problems = context.exception.problems
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].file, "journal.csv")
        self.assertEqual(problems[0].row_number, 2)
        self.assertEqual(problems[0].value, "not-a-date")
        self.assertEqual(problems[0].reason, "unparseable")

    def test_blank_date_value_raises_unparseable_as_missing(self):
        journal = self._journal(["JE-1,,1000,cash,10.00,0.00,REF-1"])
        bank = self._bank(["BT-1,2026-08-05,deposit,10.00,REF-1"])

        with self.assertRaises(UnparseableDatesError) as context:
            infer_close_period(journal, bank)

        problems = context.exception.problems
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].reason, "missing")

    def test_missing_date_header_raises_clearly(self):
        journal = _write_csv(
            self.directory,
            "journal.csv",
            "journal_id,txn_date,account_code,description,debit,credit,external_reference",
            ["JE-1,2026-08-02,1000,cash,10.00,0.00,REF-1"],
        )
        bank = self._bank(["BT-1,2026-08-05,deposit,10.00,REF-1"])

        with self.assertRaises(MissingDateColumnError) as context:
            infer_close_period(journal, bank)

        self.assertEqual(context.exception.file, "journal.csv")
        self.assertIn(DATE_COLUMN, str(context.exception))
        self.assertIn("journal.csv", str(context.exception))

    def test_empty_file_with_only_a_header_raises_clearly(self):
        journal = self._journal([])
        bank = self._bank(["BT-1,2026-08-05,deposit,10.00,REF-1"])

        with self.assertRaises(EmptyFileError) as context:
            infer_close_period(journal, bank)

        self.assertEqual(context.exception.file, "journal.csv")

    def test_completely_empty_file_raises_clearly(self):
        journal = self.directory / "journal.csv"
        journal.write_text("", encoding="utf-8")
        bank = self._bank(["BT-1,2026-08-05,deposit,10.00,REF-1"])

        with self.assertRaises(EmptyFileError) as context:
            infer_close_period(journal, bank)

        self.assertEqual(context.exception.file, "journal.csv")


if __name__ == "__main__":
    unittest.main()
