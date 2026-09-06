# CloseIQ

CloseIQ is an accounting close and reconciliation MVP that turns journal-entry and bank-transaction data into reviewable accounting exceptions.

```text
Import accounting data → run controls → persist exceptions → review via API and Power BI
```

> CloseIQ uses synthetic data only. It is not production accounting software and never automatically posts journal entries.

## What CloseIQ does

* Validates that every journal entry balances.
* Detects duplicate external references across journal entries.
* Reconciles bank transactions against cash-side ledger activity.
* Creates workflow exceptions with severity, status, source IDs, and evidence.
* Records explicit reviewer decisions: `acknowledge`, `resolve`, and `dismiss`.
* Persists accounting data, exceptions, decisions, and close-run history in PostgreSQL.
* Runs a period-aware close workflow with one command.
* Exposes close data through FastAPI, Power BI, and read-only MCP tools.

## Current controls

| Control                       | Example issue detected                                              |
| ----------------------------- | ------------------------------------------------------------------- |
| Journal balance validation    | Debits and credits do not balance                                   |
| Duplicate-reference detection | An ACH or external reference appears in multiple journals           |
| Bank reconciliation           | A bank transaction has no match or has multiple cash-ledger matches |
| Exception workflow            | A reviewer acknowledges, resolves, or dismisses a detected issue    |

## Architecture

```mermaid
flowchart LR
    A["Journal and bank CSV data"] --> B["Python imports"]
    B --> C["Accounting controls"]
    C --> D["Workflow exceptions"]
    D --> E["PostgreSQL"]
    E --> F["FastAPI and MCP"]
    F --> G["Power BI dashboard"]
```

## Tech stack

| Area                | Technology          |
| ------------------- | ------------------- |
| Accounting controls | Python              |
| Database            | PostgreSQL 17       |
| Local environment   | Docker Compose      |
| Database driver     | Psycopg             |
| API                 | FastAPI and OpenAPI |
| AI integration      | Read-only MCP tools |
| Analytics           | Power BI Desktop    |
| Testing             | Python `unittest`   |
| CI                  | GitHub Actions      |
| Version control     | Git and GitHub      |

## Quick start with Docker

Create a local `.env` file from the template:

```powershell
Copy-Item .env.example .env
```

Set a private local password in `.env`:

```text
POSTGRES_PASSWORD=your_local_password_here
```

Start PostgreSQL and the API:

```powershell
docker compose up --build -d
```

Verify that both services are running:

```powershell
docker compose ps

Invoke-RestMethod http://127.0.0.1:8000/health
```

Load the chart of accounts:

```powershell
docker compose exec api python -c "from closeiq.seed_accounts import seed_accounts; print(seed_accounts('data/chart_of_accounts.csv'))"
```

Run the full close workflow for an accounting period:

```powershell
docker compose exec api python -m closeiq.cli run-close --period 2026-08 data/journal_entries.csv data/bank_transactions.csv
```

The sample data intentionally produces four review exceptions:

* One unbalanced journal entry
* One duplicate external reference
* One ambiguous bank-to-ledger match
* One unmatched bank transaction

Review the resulting close summary and run history:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/close-summary | ConvertTo-Json

Invoke-RestMethod http://127.0.0.1:8000/close-runs | ConvertTo-Json
```

Stop the local application when finished:

```powershell
docker compose down
```

## Local Python development

Create and activate a virtual environment:

```powershell
python -m venv .venv

.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install "psycopg[binary]" python-dotenv "fastapi[standard]" "mcp[cli]>=2.1,<3"
```

Set the local source path before running application modules:

```powershell
$env:PYTHONPATH = "$PWD\src"
```

Run the close workflow locally:

```powershell
.\.venv\Scripts\python.exe -m closeiq.cli run-close --period 2026-08 data/journal_entries.csv data/bank_transactions.csv
```

## Run tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Run the API locally

```powershell
$env:PYTHONPATH = "$PWD\src"

.\.venv\Scripts\python.exe -m uvicorn closeiq.api:app --reload
```

Open the interactive API documentation at http://127.0.0.1:8000/docs.

## API endpoints

| Method | Endpoint                               | Purpose                                              |
| ------ | -------------------------------------- | ---------------------------------------------------- |
| `GET`  | `/health`                              | Confirms the API is running                          |
| `GET`  | `/exceptions`                          | Returns open close exceptions                        |
| `GET`  | `/close-summary`                       | Returns counts by workflow status                    |
| `GET`  | `/close-runs`                          | Returns auditable close-run history                  |
| `POST` | `/exceptions/{exception_id}/decisions` | Records an acknowledge, resolve, or dismiss decision |
| `GET`  | `/exceptions/{exception_id}/decisions` | Returns the decision audit history for one exception |

Example decision request:

```json
{
  "decision": "acknowledge",
  "note": "Reviewed the supporting accounting evidence."
}
```

## Close-run history

Each successful `run-close` execution creates a record in PostgreSQL containing:

* A unique close-run ID
* The accounting period, such as `2026-08`
* Journal and bank source files
* Imported journal-line and bank-transaction counts
* Total detected exception count
* Creation timestamp

This makes the close process repeatable and gives reviewers an auditable record of each run.

## Read-only MCP tools

CloseIQ includes read-only MCP tools for evidence-grounded analysis:

* `close_summary` — returns exception counts by workflow status.
* `list_open_exceptions` — returns open exceptions and supporting evidence.
* `exception_decision_history` — returns reviewer decision history for one exception.

These tools do not alter accounting records or workflow decisions.

## Power BI dashboard

The report is available at:

```text
powerbi/CloseIQ_Close_Review_Dashboard.pbix
```

It displays:

* Total, open, reviewed, resolved, and dismissed exception counts
* Open exception details and supporting reasons
* Open exceptions by severity

Keep the local API running while refreshing the report in Power BI Desktop.

## Repository structure

```text
src/closeiq/            Python application modules
tests/                  Automated tests
data/                   Synthetic journal, bank, and chart-of-accounts data
database/schema.sql     Baseline PostgreSQL schema
database/migrations/    Incremental database migrations
powerbi/                CloseIQ Power BI dashboard
docs/                   Roadmap and project documentation
.github/workflows/      GitHub Actions CI workflow
```

## Current scope

CloseIQ demonstrates an end-to-end accounting close-review workflow with synthetic data and a local single-user environment.

It does not currently include:

* Production accounting integrations
* Authentication or role-based access control
* Automatic journal posting
* Cloud deployment
* Real customer or financial data

## Next steps

Potential extensions include:

* Link exception snapshots directly to each close run
* Accept accounting data through API-driven imports
* Add authenticated reviewer identities and role-based access
* Integrate QuickBooks, Stripe, or bank sandbox APIs
* Deploy the application to a hosted environment

## Design principles

* Accounting controls detect issues; they do not silently change accounting records.
* Exceptions carry IDs, severity, status, source records, and evidence.
* Reviewer actions are explicit and auditable.
* Close runs are period-aware and reproducible.
* AI tools are read-only by default and should cite underlying accounting evidence.

### Live interactive dashboard

[Open the CloseIQ Month-End Close Review dashboard](https://app.powerbi.com/view?r=eyJrIjoiNWNjZDlmMDEtMTBlZS00OWM5LTg4OTgtMDA3NGVkMzY4MTY2IiwidCI6IjFiMGQwMmRiLWZjOWUtNDQ5NS05NTM3LTFkMzc5Y2NhMmFlNyIsImMiOjZ9&embedImagePlaceholder=true)

> Uses synthetic accounting-close data only.