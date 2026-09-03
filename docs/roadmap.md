# CloseIQ roadmap

## Project definition

**User:** a small-business controller or staff accountant completing month-end close.

**Problem:** closing data is split across a general ledger and bank activity; accountants lose time locating discrepancies and explaining them.

**Success demo:** import August data, show the trial-balance validation, identify unreconciled cash items, approve or reject an exception, and explore the results in Power BI.

## Milestone 1 — Accounting core (week 1)

- Define the chart-of-accounts and journal-entry schemas.
- Validate journal balance by `journal_id`.
- Create deterministic bank-to-ledger matching.
- Emit exceptions with a clear reason and evidence IDs.

**Done when:** a CLI processes the supplied synthetic data and tests prove an unbalanced entry and an unmatched transaction are caught.

## Milestone 2 — Data and API (week 2)

- Move records and audit events to PostgreSQL.
- Add FastAPI endpoints for uploads, close status, exceptions, and review decisions.
- Publish an OpenAPI specification and add API tests.

**Done when:** a reviewer can retrieve an exception and record an approve/reject decision through the API.

## Milestone 3 — Power BI model (week 3)

- Build a star schema: `fact_journal_line`, `fact_bank_transaction`, `fact_reconciliation_exception`, `dim_date`, `dim_account`, and `dim_vendor`.
- Create DAX measures: cash book balance, bank balance, unreconciled amount, exception count, and close completion rate.
- Build one controller dashboard page and document metric definitions.

**Done when:** a user can filter August exceptions by account and see the amount driving the difference.

## Milestone 4 — Governed AI and MCP (week 4)

- Expose read-only MCP tools: `get_trial_balance`, `list_reconciliation_exceptions`, and `explain_variance`.
- Return structured evidence with every tool result.
- Build a 20-question evaluation set and verify that answers cite tool data rather than inventing figures.

**Done when:** the AI can answer a cash-reconciliation question using tools, and refuses to post or alter entries.

## Milestone 5 — Portfolio polish (week 5)

- Add a small web interface, screenshots, and a three-minute demo video.
- Dockerize the service and automate test runs in GitHub Actions.
- Add a case-study README: problem, controls, architecture, results, and tradeoffs.

**Done when:** a recruiter can clone, run, understand, and demo the project in under ten minutes.

## Deferred on purpose

- Fine-tuning with Soup: add only after a stable baseline, and only for a bounded classification task with measured accuracy.
- Real company data, automatic journal posting, tax advice, or audit opinions.
