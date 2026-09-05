from __future__ import annotations

import argparse
import json

from .accounting import (
    find_duplicate_external_references,
    load_journal_lines,
    validate_journal_balance,
)
from .close_review import build_close_review
from .close_run import run_close
from .reconciliation import load_bank_transactions, reconcile


def main() -> None:
    parser = argparse.ArgumentParser(description="CloseIQ accounting controls")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("journal_file")

    duplicates = commands.add_parser("duplicates")
    duplicates.add_argument("journal_file")

    reconcile_command = commands.add_parser("reconcile")
    reconcile_command.add_argument("journal_file")
    reconcile_command.add_argument("bank_file")

    close_review_command = commands.add_parser("close-review")
    close_review_command.add_argument("journal_file")
    close_review_command.add_argument("bank_file")

    run_close_command = commands.add_parser("run-close")
    run_close_command.add_argument(
        "--period",
        required=True,
        help="Accounting period in YYYY-MM format, such as 2026-08",
    )
    run_close_command.add_argument("journal_file")
    run_close_command.add_argument("bank_file")

    args = parser.parse_args()

    lines = load_journal_lines(args.journal_file)

    if args.command == "validate":
        print(json.dumps(validate_journal_balance(lines), indent=2))
    elif args.command == "duplicates":
        print(json.dumps(find_duplicate_external_references(lines), indent=2))
    elif args.command == "reconcile":
        bank_transactions = load_bank_transactions(args.bank_file)
        print(json.dumps(reconcile(lines, bank_transactions), indent=2))
    elif args.command == "close-review":
        bank_transactions = load_bank_transactions(args.bank_file)
        print(json.dumps(build_close_review(lines, bank_transactions), indent=2))
    else:
        print(
            json.dumps(
                run_close(
                    args.journal_file,
                    args.bank_file,
                    close_period=args.period,
                ),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()