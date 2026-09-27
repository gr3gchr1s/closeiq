# CloseIQ v2 Architecture: Zero-Friction Upload → Deterministic Review → On-Demand AI Investigation

Status: proposed design, not yet implemented. This document describes a target
architecture and a phased path to it. Statements about "today" or "currently"
describe the repository as it exists at the time of writing; everything else
in this document is a proposal.

## 1. Product goal and non-goals

### Goal

A non-technical user — a small-business owner or bookkeeper with no software
background — can complete a month-end close review entirely through a
browser:

> Upload two accounting CSV files → click "Run Close Review" → walk away →
> return later to see complete, categorized, evidence-backed results.

At no point does that user need to run Python, use Docker, type a terminal
command, know a file path, set up a database, configure MCP, or manually
refresh Power BI. Every accounting determination (balance, duplicates,
reconciliation, severity) stays deterministic and auditable; AI is available
only as an optional, on-demand, read-only investigation aid a human explicitly
requests, never as a replacement for the accounting logic and never as
something that can change a record's state.

### Non-goals (explicitly out of scope for this document)

- **Not a multi-tenant SaaS.** No billing, no customer accounts, no
  organization-level isolation. Single deployment, single working set of
  data, matching the project's current "local, single-user" scope.
- **Not an autonomous agent.** Nothing in this design lets AI decide *when*
  to run, chain tool calls on its own initiative, or act without a human
  explicitly requesting an investigation for one specific exception.
- **Not a replacement for deterministic accounting logic.** Journal balance,
  duplicate detection, reconciliation, missing-data checks, and severity
  assignment remain plain Python, unconditionally. No phase in this roadmap
  moves any of that into an LLM.
- **Not RAG, a vector database, multi-agent orchestration, or LangGraph.**
  None of these are introduced in this design. If a future need for them
  arises, it requires its own design document and its own review of the
  auditability guarantees below.
- **Not full authentication/authorization.** This document notes where
  auth becomes necessary (once the app is reachable over a network by a
  non-technical user instead of `localhost`), but designing a full identity
  system is not covered here.
- **Not the elimination of Power BI.** Power BI remains available as an
  optional, deeper-analytics view. It stops being *required* for a user to
  see their results — that's the job of the new in-app results view.

## 2. Node-by-node workflow: upload → results display

This section lists every node in the proposed pipeline, in execution order.
Nodes marked **(existing)** already work this way in the repository today.
Nodes marked **(new)** do not exist yet and are what this design adds.

1. Upload (client) — **new**
2. Upload intake & structural validation (API boundary) — **new**
3. Job creation & persistence — **new**
4. Job orchestration (conventional code, not an agent) — **new**
5. CSV parsing & row-level validation — **extends existing loaders**
6. Reference-data check (chart of accounts) — **new validation around existing FK constraint**
7. Import persistence (journal lines, bank transactions) — **existing**
8. Deterministic control execution — **existing, extended**
9. Severity & workflow-exception assembly — **existing**
10. Exception & close-run snapshot persistence — **existing**
11. Job completion & status update — **new**
12. Results retrieval (client polling) — **new**
13. Results display (in-app) — **new**
14. On-demand AI investigation (human-initiated, later phase) — **new, Phase 3**
15. Reviewer decision (human action) — **existing, unchanged**

Detail for each node follows.

---

### 1. Upload (client)

- **Purpose:** Let the user pick two CSV files (journal entries, bank
  transactions) and a close period, and start a review with one click.
- **Input:** Two local files selected via a browser file picker; a period
  value (e.g. a month picker defaulting to the most recent complete month).
- **Output:** An HTTP multipart POST to the API.
- **Deterministic vs. AI:** Deterministic (plain web form, no AI involved).
- **Persistence requirement:** None at this node; nothing is durable until
  node 3.
- **Failure behavior:** Client-side validation blocks submission if a file
  is missing or isn't a `.csv`, with a plain-language message. No network
  call is made until both files are present.

### 2. Upload intake & structural validation (API boundary)

- **Purpose:** Accept the upload and reject obviously bad input before any
  processing begins.
- **Input:** The multipart request (two files, period string).
- **Output:** Either a rejection (HTTP 4xx with a specific reason) or a
  handoff to job creation.
- **Deterministic vs. AI:** Deterministic. Today's `create_close_run_from_upload`
  endpoint in `api.py` already performs a version of this step (filename
  presence, `.csv` suffix, period regex) — this node extends that same
  checking, it does not replace it.
- **Persistence requirement:** None yet.
- **Failure behavior:** Empty file, wrong extension, malformed period, or a
  file exceeding a defined size limit is rejected immediately with a
  specific, human-readable reason (e.g. "bank_transactions.csv is empty" —
  not a generic 500 or a raw stack trace).

### 3. Job creation & persistence

- **Purpose:** Record that a close review was requested, so the client can
  disconnect and the user can return later to the same result. This is the
  node that makes "walk away and come back" possible — it does not exist
  in the current synchronous upload endpoint, which blocks for the whole
  request.
- **Input:** Validated upload (files + period) from node 2.
- **Output:** A new `close_jobs` row (proposed table — see note below) with
  status `queued`, plus the uploaded files saved to durable storage; a
  `job_id` returned to the client immediately.
- **Deterministic vs. AI:** Deterministic.
- **Persistence requirement:** Durable. A `close_jobs` table (proposed):
  `job_id`, `status` (`queued`/`running`/`succeeded`/`failed`),
  `close_period`, `journal_source`, `bank_source`, `close_run_id`
  (nullable until success), `error_detail` (nullable), `created_at`,
  `updated_at`. Uploaded file bytes are stored (e.g. object storage or a
  server-side directory keyed by `job_id`), not just referenced by a path
  on the caller's machine.
- **Failure behavior:** If the job record or file storage write fails, the
  client receives an explicit error and no `job_id` — it must retry the
  upload. Nothing partially-queued is left behind.

### 4. Job orchestration (conventional code, not an agent)

- **Purpose:** Run the deterministic pipeline (nodes 5–10) for one job,
  updating job status as it progresses, so that a failure at any step is
  attributable to that step.
- **Input:** One `close_jobs` row with status `queued`.
- **Output:** Status transitions (`queued` → `running` → `succeeded` or
  `failed`) and, on success, a `close_run_id` pointing to the existing
  `close_runs` record.
- **Deterministic vs. AI:** Deterministic, and explicitly **not** an LLM
  agent. This is a plain function or task queue worker executing a fixed
  sequence of steps in a fixed order. No step is chosen at runtime by a
  model; the sequence is code.
- **Persistence requirement:** Every status transition is written to
  `close_jobs` as it happens (not just at the end), so a crash mid-run
  leaves a job visibly `running` (and stale) rather than silently vanished.
- **Failure behavior:** Any unhandled exception in a downstream node is
  caught at this level, written to `close_jobs.error_detail` with enough
  detail to be useful (which step, what the underlying error was), and the
  job is marked `failed`. The user sees "we couldn't process
  `journal_entries.csv`: missing required column `account_code`" — not a
  stack trace.

### 5. CSV parsing & row-level validation

- **Purpose:** Turn uploaded CSV bytes into typed rows, and catch malformed
  data (missing columns, non-numeric debit/credit, unparseable dates)
  before those rows ever reach the database.
- **Input:** The two uploaded files.
- **Output:** A list of validated `JournalLine`/`BankTransaction` records,
  or a structured list of row-level problems.
- **Deterministic vs. AI:** Deterministic.
- **Persistence requirement:** None (in-memory for this node); problems are
  surfaced through node 4's failure path.
- **Failure behavior:** Today, `load_journal_lines` and
  `load_bank_transactions` call `Decimal(row["debit"])` directly — a
  missing column raises an uncaught `KeyError`, and a non-numeric value
  raises an uncaught `decimal.InvalidOperation`. This design proposes
  wrapping that parsing in explicit per-row validation that collects every
  problem (not just the first) and reports them together, e.g. "row 4:
  `debit` is not a number ('abc')". This is new work, not something the
  current loaders already do.

### 6. Reference-data check (chart of accounts)

- **Purpose:** Confirm every `account_code` referenced by a journal line
  actually exists in the `accounts` table, with a clear error instead of a
  raw database exception.
- **Input:** Validated journal lines from node 5; the current `accounts`
  table.
- **Output:** Pass, or a structured list of unknown account codes.
- **Deterministic vs. AI:** Deterministic.
- **Persistence requirement:** Read-only against `accounts`.
- **Failure behavior:** Today this check does not exist as application
  code — it is enforced only by the `journal_lines.account_code` foreign
  key constraint in `database/schema.sql`, which means a bad account code
  currently surfaces as a raw `psycopg` integrity error inside
  `import_journal_entries`. This node proposes checking account codes
  before insert and failing the job with a specific message ("account code
  `9999` on journal `JE-2001` is not in the chart of accounts") rather than
  letting the database exception bubble up.

### 7. Import persistence (journal lines, bank transactions)

- **Purpose:** Store the period's raw accounting data.
- **Input:** Validated rows from nodes 5–6.
- **Output:** Rows in `journal_entries`, `journal_lines`, `bank_transactions`.
- **Deterministic vs. AI:** Deterministic.
- **Persistence requirement:** Durable, idempotent — this already matches
  existing behavior in `journal_import.py` and `bank_import.py`
  (`INSERT ... ON CONFLICT DO UPDATE`).
- **Failure behavior:** Existing behavior: a database error here aborts
  that import call. Under the new job model, this is caught by node 4 and
  written to `close_jobs.error_detail`.

### 8. Deterministic control execution

- **Purpose:** Run every objective accounting check against the imported
  data.
- **Input:** Imported journal lines and bank transactions for this run.
- **Output:** Raw control findings (unbalanced journals, duplicate
  references, reconciliation mismatches).
- **Deterministic vs. AI:** Fully deterministic, no exceptions. This is the
  core principle of the whole system: an LLM must never decide a
  mathematical or accounting fact that code can determine.
  - **Existing today:** journal balance validation
    (`validate_journal_balance`), duplicate external-reference detection
    (`find_duplicate_external_references`, scoped to the current import
    batch only — not cross-period history), and bank reconciliation
    (`reconcile`, exact-match only, hardcoded to cash account `1000`).
  - **Proposed additions for this phase, still deterministic Python:**
    - *Missing required data* checks (blank `account_code`, blank
      `date`, blank `description` where required) as their own control,
      distinct from the "unbalanced" check.
    - *Date/account validation* (date is a real calendar date and falls
      within the stated close period; account code is a known, active
      account) as its own control, separate from node 6's pre-import
      gate — node 6 blocks obviously-bad imports, this control reports
      softer date-range concerns as reviewable exceptions rather than
      hard failures.
- **Persistence requirement:** None at this node; findings flow to node 9.
- **Failure behavior:** A bug in a control function aborts the job (via
  node 4) rather than silently producing an incomplete result — controls
  are expected to always terminate given valid input from nodes 5–7.

### 9. Severity & workflow-exception assembly

- **Purpose:** Turn raw control findings into a single, uniformly-shaped
  exception with a deterministic severity and a stable ID.
- **Input:** Raw findings from node 8.
- **Output:** `workflow_exceptions` (existing shape: `exception_id`,
  `exception_type`, `severity`, `status`, `source_ids`, evidence fields).
- **Deterministic vs. AI:** Deterministic. This already exists as
  `build_close_review` in `close_review.py`; severity is a fixed
  rule-lookup per exception type, not a judgment call.
- **Persistence requirement:** None at this node; persisted at node 10.
- **Failure behavior:** Same as node 8 — an assembly bug aborts the job
  rather than emitting a malformed exception.

### 10. Exception & close-run snapshot persistence

- **Purpose:** Save the live, reviewable exception list and an immutable
  historical record of this run.
- **Input:** `workflow_exceptions` from node 9.
- **Output:** Rows in `close_exceptions` (live, mutable status) and
  `close_run_exceptions` (immutable snapshot for this run), plus one row in
  `close_runs`.
- **Deterministic vs. AI:** Deterministic.
- **Persistence requirement:** Durable — this already matches existing
  behavior in `exception_store.py` and `close_run.py`.
- **Failure behavior:** Existing behavior: each of these is its own
  connection/transaction today (import, exception upsert, and the
  `close_runs`/`close_run_exceptions` insert are three separate
  transactions in `run_close`) — a crash between them can leave imported
  data without a matching `close_runs` row. This design does not fix that
  gap; it's flagged here so Phase 2 (see roadmap) can decide whether to
  wrap nodes 7–10 in one transaction.

### 11. Job completion & status update

- **Purpose:** Mark the job done and make the result discoverable.
- **Input:** The `close_run_id` produced by node 10 (success) or an error
  from any earlier node (failure).
- **Output:** `close_jobs.status` set to `succeeded` (with `close_run_id`
  populated) or `failed` (with `error_detail` populated).
- **Deterministic vs. AI:** Deterministic.
- **Persistence requirement:** Durable, same `close_jobs` row from node 3.
- **Failure behavior:** This node itself is not expected to fail under
  normal operation; if the status write fails, the job is retried by the
  orchestrator rather than left in an ambiguous state.

### 12. Results retrieval (client polling)

- **Purpose:** Let the client find out when a job is done, including after
  the user has closed the browser and come back later.
- **Input:** A `job_id` (from node 3, held by the client — e.g. in the
  URL, so returning to a bookmarked link works).
- **Output:** Current job status, and once `succeeded`, the associated
  `close_run_id`.
- **Deterministic vs. AI:** Deterministic (a plain GET endpoint,
  polled or checked once on page load).
- **Persistence requirement:** Read-only against `close_jobs`.
- **Failure behavior:** Unknown `job_id` returns a clear "not found," not a
  500. A `failed` job returns its `error_detail` so the UI can show the
  user what went wrong and let them re-upload.

### 13. Results display (in-app)

- **Purpose:** Show the user their close-review results without needing
  Power BI Desktop or any manual refresh step.
- **Input:** A `close_run_id`.
- **Output:** A rendered summary (open/reviewed/resolved/dismissed counts,
  severity breakdown) and an exception list with evidence — sourced from
  the same data the existing `/close-summary`, `/exceptions`, and
  `/close-runs/{id}/exceptions` endpoints already expose.
- **Deterministic vs. AI:** Deterministic. No AI involvement in rendering
  results.
- **Persistence requirement:** Read-only.
- **Failure behavior:** If the underlying API call fails, the UI shows a
  retry option rather than a blank page. Power BI remains available
  separately for anyone who wants the deeper dashboard view, but is not on
  the path required to see results.

### 14. On-demand AI investigation (human-initiated, later phase)

- **Purpose:** Let a reviewer ask "what do we know about this specific
  exception?" and get a structured, evidence-cited explanation — not a
  new accounting determination.
- **Input:** One `exception_id`, chosen by a human clicking "Investigate"
  on that specific exception in the results view.
- **Output:** The structured AI investigation output defined in section 6.
- **Deterministic vs. AI:** AI-assisted, but bounded: the AI may only call
  the three read-only tools defined in section 4/5 and must produce output
  in the fixed schema in section 6. It cannot trigger itself — every
  invocation starts from an explicit human action on one exception.
- **Persistence requirement:** Every investigation (tool calls made,
  evidence returned, and the final structured output) is logged and
  retrievable, tied to the `exception_id` and a timestamp — this is what
  keeps the AI layer auditable.
- **Failure behavior:** A tool error or model failure surfaces as "unable
  to complete investigation" in the UI; the underlying exception is
  untouched (still `open`/whatever it was), and a human can retry or just
  proceed with a manual decision as they could before this feature existed.
- **Note:** This node is **Phase 3** work (see roadmap, section 8). It does
  not exist yet in any form — there is currently no in-app AI investigation
  feature. The existing MCP server (`mcp_server.py`) exposes read-only
  tools for *external* MCP clients (e.g. Claude Desktop) to query CloseIQ;
  it is not the same thing as this in-app, human-triggered investigation
  feature, though it establishes the same read-only precedent.

### 15. Reviewer decision (human action)

- **Purpose:** Record a human's acknowledge/resolve/dismiss decision on an
  exception.
- **Input:** `exception_id`, decision, note.
- **Output:** A new `exception_decisions` row; `close_exceptions.status`
  updated.
- **Deterministic vs. AI:** Fully human. AI investigation (node 14) may
  inform this decision by giving the human more context, but cannot make,
  suggest-and-auto-apply, or pre-fill this action on its own — the human
  reads the investigation output and decides.
- **Persistence requirement:** Durable — this already exists exactly this
  way via the `/exceptions/{exception_id}/decisions` endpoint.
- **Failure behavior:** Unchanged from today — see `api.py`'s existing
  404 handling for an unknown `exception_id`.

## 3. First three future AI tools (all read-only)

These tools do not exist in the repository today. They are the only
interface an LLM is permitted to use to gather evidence, per the boundaries
in section 7.

### `get_exception(exception_id)`

| Aspect | Specification |
| --- | --- |
| Allowed inputs | `exception_id: str` — must be a non-empty string. |
| Exact returned fields | `exception_id`, `exception_type`, `severity`, `status`, `source_ids`, `evidence` (the existing type-specific evidence object), `created_at`, `latest_decision` (nullable object: `decision`, `reviewer`, `note`, `decided_at`). |
| Authorization / scope boundary | Reads only from `close_exceptions` and, for `latest_decision`, `exception_decisions`. Exactly one exception per call — no wildcards, no bulk listing. Cannot read or infer data about any other exception. Cannot write. |
| No-result behavior | If `exception_id` does not exist, return `{"found": false, "exception_id": "<value>"}` — a normal result, not an error, since "this ID doesn't exist" is a legitimate answer. |
| Error behavior | Empty/malformed `exception_id`, or a database connectivity failure, raises a typed tool error distinct from the `found: false` case, so the calling code (and the audit log) can tell "doesn't exist" apart from "couldn't check." |

### `get_related_transactions(exception_id)`

| Aspect | Specification |
| --- | --- |
| Allowed inputs | `exception_id: str`. |
| Exact returned fields | `journal_entries`: list of `{journal_id, journal_date, description}` headers referenced by the exception's `source_ids`, each with its `journal_lines`: list of `{line_number, account_code, description, debit, credit, external_reference}`; `bank_transactions`: list of `{transaction_id, date, description, amount, external_reference}` referenced by `source_ids`. Only one of the two lists is typically non-empty, depending on `exception_type`. |
| Authorization / scope boundary | Strictly limited to rows whose ID appears in that exception's `source_ids` — this tool cannot be used to fetch arbitrary or unrelated ledger rows. Read-only. No aggregation across exceptions. |
| No-result behavior | If an exception's `source_ids` no longer resolve to any row (e.g. underlying data was removed — not possible via any current write path, but not structurally prevented either), return empty lists for the affected category with `"note": "no matching rows found for source_ids"` rather than treating it as an error. |
| Error behavior | Unknown `exception_id` uses the same `found: false` contract as `get_exception`. A database error raises a typed tool error. |

### `find_similar_transactions(account_code, amount, date_range, optional_external_reference)`

| Aspect | Specification |
| --- | --- |
| Allowed inputs | `account_code: str` (required, must exist in `accounts`); `amount: Decimal` (required); `date_range: {start: date, end: date}` (required, span capped at a fixed maximum — proposed 92 days / one quarter — to keep this a bounded lookup, not a full-history scan); `optional_external_reference: str \| None`. |
| Exact returned fields | A list of `{source: "journal_line" \| "bank_transaction", id, date, account_code (journal lines only), description, amount, external_reference, amount_difference}`, where `amount_difference` is the signed difference from the requested `amount` (0 for an exact match) and matches are limited to a fixed tolerance band (proposed: exact match only in the first version — see note below) within `account_code` and `date_range`, further filtered to `optional_external_reference` when provided. Also echoes back `{"searched": {account_code, amount, date_range, optional_external_reference}}` so the caller can see exactly what was searched. |
| Authorization / scope boundary | Read-only; bounded by the mandatory `account_code` and capped `date_range` — this tool cannot be used to browse the whole ledger. Results are capped at a fixed maximum row count (proposed: 50) to prevent an unbounded result set. |
| No-result behavior | Zero matches within the bound returns an empty list plus the echoed `searched` parameters — this is a normal, informative result, not an error. |
| Error behavior | `account_code` not present in `accounts` raises a typed input-validation error (distinct from "found nothing" — an invalid account is not the same as a valid account with no matches). A `date_range` exceeding the maximum span, or a malformed date/amount, is rejected with a typed error rather than silently truncated or coerced. |

Note on tolerance: the first version of this tool should default to **exact
amount match** within the account and date bound, since "similar" is
otherwise a judgment call this document explicitly keeps out of the tool
layer. If a fuzzy tolerance is added later, it must be a fixed, documented,
code-defined band (e.g. ±$0.01 for rounding) — never something the model
adjusts per query.

## 4. Structured AI investigation output

Every AI investigation (node 14) must produce exactly this shape. No field
is optional; a tool/model that cannot populate a field must say so
explicitly rather than omit it.

| Field | Type | Meaning |
| --- | --- | --- |
| `exception_id` | string | The exception this investigation was run for. |
| `summary` | string | One or two plain-English sentences describing the situation, written for the reviewer, not restating raw JSON. |
| `evidence_used` | list of `{tool, arguments, result_reference}` | Every tool call made during the investigation, in order, with enough detail (e.g. row IDs returned) to let a human re-run and verify the same lookup. This is the audit trail for the investigation. |
| `what_is_known` | list of strings | Facts directly supported by `evidence_used` — e.g. "JE-1004 has a $50.00 debit with no offsetting credit line." |
| `what_is_uncertain` | list of strings | Anything the evidence doesn't settle — e.g. "no note on the journal entry explains the missing offset; could be a data-entry omission or an intentionally split entry." |
| `recommended_next_step` | string | A single, concrete suggested action for a human (e.g. "confirm with the preparer whether a second line was omitted"). Never a decision the AI has itself taken. |
| `confidence` | one of `"low"`, `"medium"`, `"high"` | The model's self-assessed confidence in `what_is_known`, not in what the reviewer should do. Fixed enum, not a raw numeric score, to avoid false precision. |
| `human_review_required` | boolean | Always `true` in this design. Present explicitly (not just implied) so the UI has a field to key off, and so this document's boundary in section 5 ("AI may not resolve exceptions") is enforced structurally, not just by convention. |

## 5. Explicit boundaries: what AI must never do

- Never decide, override, or adjust a mathematical or accounting fact
  (balance status, duplicate status, reconciliation match/no-match,
  severity) — those remain exclusively the output of the deterministic
  nodes in section 2.
- Never call `POST /exceptions/{id}/decisions` or any other write/mutating
  endpoint, directly or indirectly. The three tools in section 3 are
  read-only by construction (`SELECT`-only queries) — there must be no code
  path that lets an investigation result automatically create a decision.
- Never mark an exception `reviewed`, `resolved`, or `dismissed`. Only node
  15 (a human action) may do this.
- Never edit or delete a journal entry, bank transaction, account, close
  run, or prior decision.
- Never fabricate a number, ID, date, or account code not returned by one
  of the three tools. `evidence_used` exists specifically so this is
  checkable.
- Never run without an explicit, single-exception human request. No
  scheduled, automatic, or bulk investigation runs in this design.
- Never chain into a second exception's data unless that data was already
  returned as part of `get_related_transactions` for the requested
  exception. No open-ended exploration of the database.
- Never retain state between investigations that changes future
  deterministic behavior (e.g. no "learned" adjustment to severity rules).
  Each investigation is independent and stateless with respect to the
  deterministic pipeline.

## 6. Phased implementation roadmap

### Phase 0 — Current state (already true today)

CLI + Docker Compose + FastAPI + Postgres + Power BI + read-only MCP tools,
operated by a technical user. Includes a working (synchronous) file-upload
API endpoint (`POST /close-runs`), auto-seeded chart of accounts, and an
idempotent `demo` CLI command. This is the baseline this document builds on.

### Phase 1 — Non-technical upload-and-run workflow, no AI

This is the first build phase, matching the requirement that the
user-facing workflow ships before any AI investigation work begins.

- Add the `close_jobs` table and the job-orchestration node (sections 2.3–2.4).
- Add row-level CSV validation and the reference-data check (sections 2.5–2.6),
  replacing raw `KeyError`/`decimal.InvalidOperation`/DB-integrity-error
  failures with structured, human-readable errors.
- Add the missing-required-data and date/account-validation controls
  (section 2.8) as new deterministic checks.
- Build a minimal web front end: an upload form, a "Run Close Review"
  button, a results page that polls job status and then renders the
  summary/exception list (sections 2.1, 2.12–2.13) using data already
  exposed by the existing API endpoints.
- Host the app so a non-technical user reaches it via a URL, with Docker/
  Postgres/FastAPI running as invisible backend infrastructure rather than
  something the end user sets up.
- **Explicitly deferred to Phase 2 or later:** fixing the cross-node
  transaction gap noted in section 2.10, generalizing the hardcoded cash
  account, and any authentication — none of these block the "upload → run →
  walk away → come back" experience, but they should be resolved before
  real (non-synthetic) data or multiple concurrent users are in play.

### Phase 2 — Operational hardening

- Wrap import → controls → persistence (today's separate transactions) in
  one atomic unit, or add compensating cleanup, so a mid-run crash can't
  leave orphaned imported data.
- Extend duplicate-reference detection to check against prior periods'
  data already in Postgres, not just the current import batch.
- Make the reconciliation cash account configurable instead of hardcoded
  to `1000`.
- Add basic authentication/access control now that the app is reachable
  over a network rather than only `localhost`.
- Add explicit decision-transition rules to the existing decisions
  endpoint (e.g. reject `dismiss` on an already-`resolved` exception, or
  make such transitions an explicit, logged override).

### Phase 3 — Read-only AI investigation

- Implement the three tools in section 3 against the existing schema.
- Add the "Investigate" action in the results UI (section 2.14), wired to
  produce the structured output in section 4.
- Add investigation logging (tool calls, evidence, output) as its own
  durable record, satisfying the auditability requirement in section 5.
- Everything in section 5's boundary list is enforced and, where feasible,
  covered by an automated test (e.g. a test asserting the AI tool module
  contains no `INSERT`/`UPDATE`/`DELETE` and no call to the decisions
  endpoint).

### Explicitly not scheduled in this roadmap

RAG, a vector database, autonomous multi-step agents, multi-agent
orchestration, and LangGraph are not part of Phase 1, 2, or 3. Introducing
any of them requires a new design document that re-examines the
auditability and "AI never decides accounting facts" guarantees this
document establishes.

## 7. Definition of Done

For Phase 1 (the upload-and-run workflow) specifically:

1. A user with no Python, Docker, terminal, or database experience can
   upload two CSVs and click one button in a browser to start a close
   review, with no other setup step.
2. After clicking "Run," the user can close the browser tab and return
   later (e.g. via a saved link) to see the same completed result, without
   re-running anything.
3. Every deterministic control (balance, duplicates, reconciliation,
   missing-required-data, date/account validation) executes as plain
   Python with zero LLM calls anywhere in that code path — verifiable by
   inspection (no AI client import or call in the deterministic modules).
4. A malformed upload (missing column, non-numeric amount, unknown account
   code, blank required field) produces a specific, human-readable error
   surfaced in the UI — never a raw stack trace, a generic 500, or a
   silently-swallowed failure.
5. A job's full lifecycle (queued → running → succeeded/failed, including
   which node failed and why on failure) is stored and retrievable after
   the fact.
6. The in-app results view shows the same open-exception data as the
   existing `/exceptions` and `/close-summary` endpoints, without requiring
   Power BI Desktop to be opened or refreshed.
7. All existing automated tests continue to pass, and new tests exist for:
   job creation, at least one row-level validation failure, and the
   unknown-account-code failure.
8. No code path in this phase calls, or could call, an LLM — Phase 1 ships
   with zero AI dependency, confirmed by the absence of any model-API
   client in the dependency list for this phase's code.

Additional checks that apply once Phase 3 (AI investigation) ships:

9. Each of the three read-only AI tools has automated tests covering: a
   found result, a not-found result, and an invalid-input/error result.
10. Every AI investigation's `evidence_used` can be replayed (same tool,
    same arguments) by a human and returns the same underlying rows, and no
    investigation ever results in a `close_exceptions.status` change
    without a separate, explicit human decision call.

## Assumptions

- "Walk away and return" is interpreted as requiring an asynchronous job
  model with a durable, pollable status — not merely a slow synchronous
  request. This is why sections 2.3–2.4 and 2.11–2.12 introduce a
  `close_jobs` concept that does not exist in the repository today.
- The proposed `close_jobs` table, its columns, the specific validation
  error messages, the 92-day/50-row bounds on `find_similar_transactions`,
  and the exact hosting mechanism are this document's proposals, not
  existing schema, endpoints, or configuration — none of them appear in
  `database/schema.sql` or `src/closeiq/` today.
- "Non-technical user" is assumed to mean the *end user running a close
  review* has zero technical setup steps; someone still has to deploy and
  operate the hosted instance (a technical concern addressed by Phase 1's
  hosting step, not by removing infrastructure entirely).
- Phase 1 assumes small-to-moderate CSV sizes (consistent with the
  project's current synthetic sample data) — this document does not
  address large-file streaming or long-running-job scaling; if job runtimes
  become long, `close_jobs` already supports moving from an in-process
  background task to a real task queue without changing the node contract.