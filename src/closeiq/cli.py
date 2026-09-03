from __future__ import annotations

import argparse
import json

from .accounting import (
    find_duplicate_external_references,
    load_journal_lines,
    validate_journal_balance,
)
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

    args = parser.parse_args()

    lines = load_journal_lines(args.journal_file)

    if args.command == "validate":
        print(json.dumps(validate_journal_balance(lines), indent=2))
    elif args.command == "duplicates":
        print(json.dumps(find_duplicate_external_references(lines), indent=2))
    else:
        bank_transactions = load_bank_transactions(args.bank_file)
        print(json.dumps(reconcile(lines, bank_transactions), indent=2))


if __name__ == "__main__":
    main()