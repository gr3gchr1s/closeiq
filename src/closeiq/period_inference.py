from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path


# Both journal_entries.csv and bank_transactions.csv (and their demo_*
# equivalents) use a column literally named "date" — not the Postgres
# column names journal_entries.journal_date /
# bank_transactions.transaction_date, which only exist after import
# (journal_import.py, bank_import.py assign them from this same CSV
# column). See docs/phase_1b_spec.md, section 4 (WS-A).
DATE_COLUMN = "date"

CLOSE_PERIOD_FORMAT = "%Y-%m"


class PeriodInferenceError(Exception):
    """Base class for every error this module raises."""


@dataclass(frozen=True)
class DateProblem:
    """One row's date that could not be used to infer a close period."""

    file: str
    row_number: int  # 1-indexed over data rows; the header is not row 1
    value: str
    reason: str  # "missing" or "unparseable"


class UnparseableDatesError(PeriodInferenceError):
    """One or more rows have a missing or unparseable date value.

    Carries every such row (not just the first) across both files, with
    enough detail (file, row number, raw value, reason) for the API layer
    to build a specific, human-readable 422 response later.
    """

    def __init__(self, problems: list[DateProblem]):
        self.problems = problems
        super().__init__(
            f"{len(problems)} row(s) have a missing or unparseable date: "
            + "; ".join(
                f"{problem.file} row {problem.row_number}: "
                f"{problem.reason} ({problem.value!r})"
                for problem in problems
            )
        )


class PeriodAmbiguousError(PeriodInferenceError):
    """The uploaded files' dates span more than one calendar month."""

    def __init__(self, months: list[str]):
        self.months = months
        super().__init__(
            "Uploaded files span multiple calendar months: "
            + ", ".join(months)
        )


class MissingDateColumnError(PeriodInferenceError):
    """A file has no column literally named "date"."""

    def __init__(self, file: str):
        self.file = file
        super().__init__(f"{file} has no '{DATE_COLUMN}' column")


class EmptyFileError(PeriodInferenceError):
    """A file has no data rows to infer a period from."""

    def __init__(self, file: str):
        self.file = file
        super().__init__(f"{file} is empty")


def _collect_months_and_problems(
    path: str | Path,
) -> tuple[set[str], list[DateProblem]]:
    file_label = Path(path).name

    with open(path, newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = reader.fieldnames

        if not fieldnames:
            raise EmptyFileError(file_label)
        if DATE_COLUMN not in fieldnames:
            raise MissingDateColumnError(file_label)

        rows = list(reader)

    if not rows:
        raise EmptyFileError(file_label)

    months: set[str] = set()
    problems: list[DateProblem] = []

    for row_number, row in enumerate(rows, start=2):  # row 1 is the header
        raw_value = row.get(DATE_COLUMN)

        if raw_value is None or not raw_value.strip():
            problems.append(
                DateProblem(file_label, row_number, raw_value or "", "missing")
            )
            continue

        try:
            parsed_date = date.fromisoformat(raw_value.strip())
        except ValueError:
            problems.append(
                DateProblem(file_label, row_number, raw_value, "unparseable")
            )
            continue

        months.add(parsed_date.strftime(CLOSE_PERIOD_FORMAT))

    return months, problems


def infer_close_period(
    journal_path: str | Path,
    bank_path: str | Path,
) -> str:
    """Infer a YYYY-MM close period from both files' "date" column.

    Reads only the "date" column of each file — a lightweight pre-parse,
    not full row validation (that is Phase 1C's job; see
    docs/phase_1b_spec.md, section 4, WS-A). Raises one of this module's
    typed errors (never a bare exception) on every failure case:

    - `EmptyFileError` — a file has no data rows (or is fully empty).
    - `MissingDateColumnError` — a file has no "date" column.
    - `UnparseableDatesError` — one or more rows (across either file)
      have a blank or unparseable date; carries every such row.
    - `PeriodAmbiguousError` — the dates found (combined across both
      files) span more than one calendar month; carries the sorted
      distinct months.

    Returns the single common `YYYY-MM` period only when both files
    parsed cleanly and every date found falls in the same calendar
    month.
    """
    journal_months, journal_problems = _collect_months_and_problems(
        journal_path
    )
    bank_months, bank_problems = _collect_months_and_problems(bank_path)

    problems = journal_problems + bank_problems
    if problems:
        raise UnparseableDatesError(problems)

    months = journal_months | bank_months
    if len(months) > 1:
        raise PeriodAmbiguousError(sorted(months))

    return next(iter(months))
