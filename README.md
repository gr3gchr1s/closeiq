# CloseIQ

CloseIQ is a portfolio-scale accounting close and reconciliation platform. It turns imported general-ledger and bank data into validated accounting records, reconciliation exceptions, and dashboard-ready outputs.

## MVP outcome

For a selected accounting period, CloseIQ will:

1. validate that every journal entry balances;
2. match bank activity to cash-side ledger entries;
3. surface unmatched or ambiguous items as reviewable exceptions; and
4. retain a decision-ready audit trail.

The initial build uses synthetic data only. It is **not** a production accounting system and does not automatically post journal entries.

## Architecture direction

| Layer | Initial choice | Later extension |
| --- | --- | --- |
| Ingestion | CSV | QuickBooks, Stripe, or bank sandbox API |
| Accounting rules | Python | versioned rules and approval workflow |
| Storage | CSV fixtures | PostgreSQL |
| Service | CLI | FastAPI + OpenAPI |
| Analytics | dashboard-ready CSV | Power BI star schema + DAX |
| AI | none in MVP | read-only MCP tools and evidence-grounded analyst |

## Run the first vertical slice

```bash
cd closeiq
python -m closeiq.cli validate data/journal_entries.csv
python -m closeiq.cli reconcile data/journal_entries.csv data/bank_transactions.csv
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

## Non-negotiable controls

- Double-entry validation happens before analysis.
- Matching produces a reviewable exception; it never changes accounting records.
- Every future AI response must cite the rule, transaction IDs, and calculation that support it.
- Writes will require explicit human approval and be logged.

See [docs/roadmap.md](docs/roadmap.md) for milestones and completion criteria.
