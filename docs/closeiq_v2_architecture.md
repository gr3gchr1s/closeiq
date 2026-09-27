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
7. Import persistence (journal lines, bank transactions) — **extends existing loaders, run-scoped (see section 3)**
8. Deterministic control execution — **existing, extended**
9. Severity & workflow-exception assembly — **existing**
10. Exception & close-run snapshot persistence — **extends existing, now atomic with nodes 7–9 (see section 3)**
11. Job completion & status update — **new**
12. Results retrieval (client polling) — **new**
13. Results display (in-app) — **new**
14. On-demand AI investigation (human-initiated, later phase) — **new, Phase 3**
15. Reviewer decision (human action) — **existing, unchanged**

Detail for each node follows.

---

### 1. Upload (client)

- **Purpose:** Let the user pick two CSV files (journal entries, bank
  transactions) and start a review with one click, without requiring them
  to know or enter a close period up front.
- **Input:** Two local files selected via a browser file picker. No period
  field is shown at this step; period is determined at node 2 from the
  file contents (see below).
- **Output:** An HTTP multipart POST to the API (files only).
- **Deterministic vs. AI:** Deterministic (plain web form, no AI involved).
- **Persistence requirement:** None at this node; nothing is durable until
  node 3.
- **Failure behavior:** Client-side validation blocks submission if a file
  is missing or isn't a `.csv`, with a plain-language message. No network
  call is made until both files are present.
- **Close-period selection (conditional):** If node 2's period inference
  (below) reports that the uploaded files span more than one calendar
  month or contain inconsistent/unparseable dates, the client shows a
  month picker and requires an explicit choice before resubmitting. If
  every date in both files falls within a single calendar month, no
  picker is shown at all — the period is inferred automatically and the
  user never sees this step. This replaces the earlier "period value"
  input in the initial upload request.

### 2. Upload intake & structural validation (API boundary)

- **Purpose:** Accept the upload, reject obviously bad input before any
  processing begins, and determine the close period from file contents.
- **Input:** The multipart request (two files, no period string — see node
  1's revised close-period handling).
- **Output:** Either a rejection (HTTP 4xx with a specific reason), a
  request for the client to supply an explicit period (see below), or a
  handoff to job creation with an inferred or confirmed period attached.
- **Deterministic vs. AI:** Deterministic. Today's `create_close_run_from_upload`
  endpoint in `api.py` already performs a version of the file-shape checks
  (filename presence, `.csv` suffix) and today accepts an explicit period
  string with regex validation; this node extends that checking and adds
  the period-inference step below, it does not replace the file-shape
  checks.
- **Close-period inference (new):** Scan the `journal_date` column of the
  journal file and the `transaction_date` column of the bank file (a
  lightweight pre-parse, ahead of node 5's full row validation). If every
  date across both files falls within a single calendar month, that month
  is the close period — no user input required, and this is what makes
  the one-click workflow in section 1's product goal possible. If dates
  span more than one calendar month, or a date is unparseable at this
  pre-parse stage, this node returns a specific response (HTTP 422 with
  `reason: "period_ambiguous"` or `reason: "unparseable_dates"`, plus the
  distinct months or bad rows found) instead of creating a job; the client
  uses this to show the month picker described in node 1 and resubmit with
  an explicit `close_period` field. A resubmission with an explicit period
  is accepted as-is (that period is used verbatim; this node does not
  re-validate that every row falls inside it — full row-level date/period
  checks remain node 8's job, per the "date/account validation" control).
- **Persistence requirement:** None yet.
- **Failure behavior:** Empty file, wrong extension, a file exceeding a
  defined size limit, or the period-ambiguity cases above are rejected
  immediately with a specific, human-readable reason (e.g.
  "bank_transactions.csv is empty", or "journal_entries.csv contains dates
  in both 2025-03 and 2025-04 — choose a close period") — not a generic
  500 or a raw stack trace.

### 3. Job creation & persistence

- **Purpose:** Record that a close review was requested, so the client can
  disconnect and the user can return later to the same result. This is the
  node that makes "walk away and come back" possible — it does not exist
  in the current synchronous upload endpoint, which blocks for the whole
  request.
- **Input:** Validated upload (files + inferred-or-explicit period) from
  node 2.
- **Output:** A new `close_jobs` row (proposed table — see note below) with
  status `queued`, plus the uploaded files saved to durable storage; a
  `job_id` returned to the client immediately. `job_id` doubles as the
  **import batch identifier** used throughout section 3 (Run-level data
  isolation and idempotency) — every row imported for this upload is
  tagged with this same `job_id`, which is what lets two uploads with
  overlapping source IDs coexist instead of overwriting each other.
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
  Nodes 7–10 (import through close-run snapshot persistence) execute as
  one atomic unit relative to this job — see node 7 and node 10 for the
  mechanism. This is a required Phase 1A property, not a later hardening
  step (see section 7, Phase 1A).
- **Failure behavior:** Any unhandled exception in a downstream node is
  caught at this level, written to `close_jobs.error_detail` with enough
  detail to be useful (which step, what the underlying error was), and the
  job is marked `failed`. The user sees "we couldn't process
  `journal_entries.csv`: missing required column `account_code`" — not a
  stack trace. Because nodes 7–10 are atomic, a `failed` job never leaves
  imported journal/bank rows in the database without a corresponding
  `close_runs` row — either both exist or neither does.

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

- **Purpose:** Store the period's raw accounting data, tagged to this
  job's import batch so it cannot be silently overwritten by an unrelated
  later upload (see section 3, Run-level data isolation and idempotency).
- **Input:** Validated rows from nodes 5–6; the current job's `job_id`.
- **Output:** Rows in `journal_entries`, `journal_lines`,
  `bank_transactions`, each tagged with `import_batch_id = job_id` per
  section 3's proposed key change.
- **Deterministic vs. AI:** Deterministic.
- **Persistence requirement:** Durable, and part of the single atomic unit
  spanning nodes 7–10 (see below) — this changes existing behavior in
  `journal_import.py` and `bank_import.py`, which today upsert with
  `INSERT ... ON CONFLICT DO UPDATE` against a *global* key and commit
  independently of node 10. Per section 3, uniqueness moves to
  `(import_batch_id, journal_id)` / `(import_batch_id, bank_transaction_id)`,
  so this insert is a plain `INSERT` (no legitimate conflict can occur
  within a fresh batch) rather than an upsert.
- **Failure behavior:** This node, node 8 (controls), node 9 (exception
  assembly), and node 10 (close-run + snapshot persistence) run inside one
  database transaction (or, if the orchestrator's execution model cannot
  hold one connection/transaction open across all four steps, an
  explicitly staged sequence with a documented compensating cleanup that
  deletes any rows written under this `import_batch_id` on failure — see
  section 7, Phase 1A, for which of the two this project adopts). Either
  way, a database error at this node — or any node through node 10 —
  results in **no** durable trace of this job's import: no orphaned
  `journal_entries`/`journal_lines`/`bank_transactions` rows survive a
  `failed` job. This replaces the previous design's three-independent-
  transaction behavior, which is no longer acceptable at any phase.

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
  `close_runs` (including the `import_batch_id` this run was produced
  from, per section 3).
- **Deterministic vs. AI:** Deterministic.
- **Persistence requirement:** Durable, and the closing step of the single
  atomic unit described at node 7. This is a required change from today's
  behavior, not an optional hardening step.
- **Failure behavior:** Today, import (node 7), exception upsert (node 9's
  persistence), and the `close_runs`/`close_run_exceptions` insert are
  three separate connections/transactions in `run_close`, so a crash
  between them can leave imported data without a matching `close_runs`
  row. This design requires that gap be closed as part of Phase 1A (see
  section 7) — not deferred — using one of two approaches:
  1. **Single transaction:** nodes 7–10 share one database connection and
     one transaction, committed only after node 10 succeeds, rolled back
     automatically on any exception in nodes 7–10.
  2. **Staged approach with documented cleanup:** if a single long-lived
     transaction is impractical (e.g. nodes 7–10 must run as separate
     steps for operational reasons), each step's writes are tagged with
     the job's `import_batch_id`/`close_run_id`, and node 4's failure
     handler explicitly deletes every row written under that ID (from
     `journal_lines`, `journal_entries`, `bank_transactions`,
     `close_exceptions`, `close_run_exceptions`, and any partial
     `close_runs` row) before marking the job `failed`. This cleanup path
     must itself be tested (a forced failure at each of nodes 8, 9, and 10
     leaves zero rows behind for that `import_batch_id`).
  Phase 1A must pick one of these two (see the open question in section 7)
  and implement it — a `failed` job must never leave imported accounting
  data without a corresponding close-run result.

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
- **Input:** One `(close_run_id, exception_id)` pair, chosen by a human
  clicking "Investigate" on that specific exception in the results view
  (the results view already knows which `close_run_id` it is showing, per
  node 13).
- **Output:** The structured AI investigation output defined in section 5.
- **Deterministic vs. AI:** AI-assisted, but bounded: the AI may only call
  the three read-only tools defined in section 4 and must produce output
  in the fixed schema in section 5. It cannot trigger itself — every
  invocation starts from an explicit human action on one exception within
  one run.
- **Persistence requirement:** Every investigation (tool calls made,
  evidence returned, and the final structured output) is logged and
  retrievable, tied to the `close_run_id`, `exception_id`, and a timestamp
  — this is what keeps the AI layer auditable.
- **Failure behavior:** A tool error or model failure surfaces as "unable
  to complete investigation" in the UI; the underlying exception is
  untouched (still `open`/whatever it was), and a human can retry or just
  proceed with a manual decision as they could before this feature existed.
- **Note:** This node is **Phase 3** work (see section 7's roadmap). It does
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

## 3. Run-level data isolation and idempotency

### The problem

`journal_entries.journal_id` and `bank_transactions.bank_transaction_id`
are globally-keyed primary keys in `database/schema.sql` today, with no
column tying a row to the run or upload that created it. Import today
upserts against those global keys (`INSERT ... ON CONFLICT DO UPDATE` in
`journal_import.py` / `bank_import.py`). That means a later upload that
happens to reuse an ID from an earlier upload — the same journal ID
reused across periods, a corrected re-export of last month's file, two
unrelated close periods that both use `JE-1001` as a numbering
convention — silently **overwrites** the earlier row in place. The
earlier close run's `close_run_exceptions` snapshot still exists (it's
already an immutable copy), but anything that re-reads live data by
`journal_id`/`bank_transaction_id` — including the future AI evidence
tools in section 4 — would see the overwritten row, not the row that
existed when that earlier run executed.

### Proposed design: import-batch-scoped keys

Tag every imported row with the `import_batch_id` established at node 3
(section 2) — in the minimal version of this design, `import_batch_id`
*is* the upload's `job_id`, so no new identifier concept is introduced.

- Add `import_batch_id` to `journal_entries` and `bank_transactions`.
- Change uniqueness from a bare global key to a composite key scoped to
  the batch: `journal_entries` becomes unique on
  `(import_batch_id, journal_id)` instead of `journal_id` alone; likewise
  `bank_transactions` becomes unique on
  `(import_batch_id, bank_transaction_id)`. `journal_lines` continues to
  reference its parent journal entry, now via
  `(import_batch_id, journal_id)`.
- Add `import_batch_id` to `close_runs`, populated at node 10, so that
  given a `close_run_id` it is always possible to find the exact set of
  source rows that run was computed from.
- Import (node 7) becomes a plain `INSERT`, not an upsert: within a fresh
  `import_batch_id`, no legitimate conflict can occur, since the batch has
  never been imported before.

### How re-running the same files behaves

Re-uploading the identical two CSVs a second time creates a **new**
`job_id` (node 3) and therefore a new `import_batch_id` — it is not
treated as "the same" upload, even if every `journal_id` and
`bank_transaction_id` inside the files is identical to the first upload.
The result is a second, independent close run with its own rows, its own
exceptions, and its own `close_runs` entry, fully coexisting with the
first. This is a deliberate trade-off this design makes explicitly: the
project gives up the old global-key idempotency (upload the same file
twice, get one updated row) in exchange for run-level auditability
(upload the same file twice, get two independently inspectable runs,
neither able to corrupt the other). If true "re-run this exact upload and
update it in place" idempotency is wanted later, it requires a separate,
explicit mechanism (e.g. a content hash of the uploaded files that maps
to an existing `import_batch_id` and is surfaced to the user as "this
looks identical to job `<id>` — view that result instead?") — not
in scope for Phase 1.

### Auditability across overlapping source IDs

Because uniqueness is scoped to `(import_batch_id, journal_id)` rather
than `journal_id` alone, two runs that happen to reuse the same
`journal_id` (or `bank_transaction_id`) each keep their own row,
permanently. Neither run's data is mutated by the other. Both remain
independently queryable by joining through their respective
`import_batch_id`, and both remain visible in full via their
`close_run_exceptions` snapshot regardless of what any later upload does.

### Scoping future AI evidence retrieval to the selected run

The three tools in section 4 (`get_exception`, `get_related_transactions`,
`find_similar_transactions`) must resolve source rows via the
`import_batch_id` recorded on the `close_runs` row that owns the
exception being investigated — found by joining
`close_exceptions`/`close_run_exceptions` → `close_runs` →
`import_batch_id` — and must filter `journal_entries` /
`bank_transactions` by that exact `import_batch_id`. They must never look
up a source row by bare `journal_id`/`bank_transaction_id` alone, since
under this design that value is no longer guaranteed unique across runs.
This is what prevents an investigation from accidentally citing a
different run's overwritten-in-appearance-only row as if it were the
evidence for the run actually being reviewed.

## 4. First three future AI tools (all read-only)

These tools do not exist in the repository today. They are the only
interface an LLM is permitted to use to gather evidence, per the boundaries
in section 6, and are scoped per section 3 above.

### `get_exception(close_run_id, exception_id)`

| Aspect | Specification |
| --- | --- |
| Allowed inputs | `close_run_id: str` (the run the investigation was launched from, per node 13/14); `exception_id: str` — must be a non-empty string. |
| Exact returned fields | `exception_id`, `exception_type`, `severity`, `status`, `source_ids`, `evidence` (the existing type-specific evidence object), `created_at`, `latest_decision` (nullable object: `decision`, `reviewer`, `note`, `decided_at`). |
| Authorization / scope boundary | Reads the run-scoped snapshot from `close_run_exceptions` for `(close_run_id, exception_id)` — not `close_exceptions` directly — so the evidence reflects exactly what this run found, not whatever `close_exceptions` (live, mutable) currently holds; `status`/`latest_decision` are still read live (from `close_exceptions`/`exception_decisions`) since decisions are intentionally not run-scoped. Exactly one exception per call — no wildcards, no bulk listing. Cannot read or infer data about any other exception. Cannot write. |
| No-result behavior | If `(close_run_id, exception_id)` does not exist, return `{"found": false, "close_run_id": "<value>", "exception_id": "<value>"}` — a normal result, not an error, since "this ID doesn't exist" is a legitimate answer. |
| Error behavior | Empty/malformed `close_run_id` or `exception_id`, or a database connectivity failure, raises a typed tool error distinct from the `found: false` case, so the calling code (and the audit log) can tell "doesn't exist" apart from "couldn't check." |

### `get_related_transactions(close_run_id, exception_id)`

| Aspect | Specification |
| --- | --- |
| Allowed inputs | `close_run_id: str`; `exception_id: str`. |
| Exact returned fields | `journal_entries`: list of `{journal_id, journal_date, description}` headers referenced by the exception's `source_ids`, each with its `journal_lines`: list of `{line_number, account_code, description, debit, credit, external_reference}`; `bank_transactions`: list of `{transaction_id, date, description, amount, external_reference}` referenced by `source_ids`. Only one of the two lists is typically non-empty, depending on `exception_type`. |
| Authorization / scope boundary | Strictly limited to rows whose ID appears in that exception's `source_ids`, **and** filtered to the `import_batch_id` recorded on the `close_runs` row for `close_run_id` (per section 3) — this tool cannot be used to fetch arbitrary or unrelated ledger rows, and cannot return a different run's row even if IDs collide. Read-only. No aggregation across exceptions. |
| No-result behavior | If an exception's `source_ids` no longer resolve to any row within that run's `import_batch_id` (e.g. underlying data was removed — not possible via any current write path, but not structurally prevented either), return empty lists for the affected category with `"note": "no matching rows found for source_ids in this run"` rather than treating it as an error. |
| Error behavior | Unknown `(close_run_id, exception_id)` uses the same `found: false` contract as `get_exception`. A database error raises a typed tool error. |

### `find_similar_transactions(close_run_id, source, amount, date_range, account_code, optional_external_reference)`

Bank transactions have no `account_code` column (`database/schema.sql`),
so a single `account_code`-keyed search cannot cover both source types.
This tool instead takes an explicit `source` mode:

| Aspect | Specification |
| --- | --- |
| Allowed inputs | `close_run_id: str` (required — see scope boundary below); `source: "journal_line" \| "bank_transaction"` (required, selects which table is searched); `amount: Decimal` (required); `date_range: {start: date, end: date}` (required, span capped at a fixed maximum — proposed 92 days / one quarter — to keep this a bounded lookup, not a full-history scan); `account_code: str` (**required when `source == "journal_line"`, must exist in `accounts`; must be omitted/`None` when `source == "bank_transaction"`, since that table has no account code**); `optional_external_reference: str \| None`. |
| Exact returned fields | A list of `{source, id, date, account_code (present only when `source == "journal_line"`), description, amount, external_reference, amount_difference}`, where `amount_difference` is the signed difference from the requested `amount` (0 for an exact match) and matches are limited to a fixed tolerance band (proposed: exact match only in the first version — see note below), filtered to `optional_external_reference` when provided. Also echoes back `{"searched": {close_run_id, source, amount, date_range, account_code, optional_external_reference}}` so the caller can see exactly what was searched. |
| Authorization / scope boundary | Read-only; results are limited to rows within the `import_batch_id` of `close_run_id` (per section 3) — this tool searches the run being investigated, not the full historical ledger, so it cannot be used to browse data outside the run a human explicitly opened an investigation for. Further bounded by the capped `date_range` and, for `source == "journal_line"`, the mandatory `account_code`. Results are capped at a fixed maximum row count (proposed: 50). |
| No-result behavior | Zero matches within the bound returns an empty list plus the echoed `searched` parameters — this is a normal, informative result, not an error. |
| Error behavior | `account_code` supplied with `source == "bank_transaction"`, or omitted with `source == "journal_line"`, raises a typed input-validation error. `account_code` not present in `accounts` raises a typed input-validation error (distinct from "found nothing"). A `date_range` exceeding the maximum span, or a malformed date/amount, is rejected with a typed error rather than silently truncated or coerced. |

Note on tolerance: the first version of this tool should default to **exact
amount match** within the account and date bound, since "similar" is
otherwise a judgment call this document explicitly keeps out of the tool
layer. If a fuzzy tolerance is added later, it must be a fixed, documented,
code-defined band (e.g. ±$0.01 for rounding) — never something the model
adjusts per query.

## 5. Structured AI investigation output

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
| `human_review_required` | boolean | Always `true` in this design. Present explicitly (not just implied) so the UI has a field to key off, and so this document's boundary in section 6 ("AI may not resolve exceptions") is enforced structurally, not just by convention. |

## 6. Explicit boundaries: what AI must never do

- Never decide, override, or adjust a mathematical or accounting fact
  (balance status, duplicate status, reconciliation match/no-match,
  severity) — those remain exclusively the output of the deterministic
  nodes in section 2.
- Never call `POST /exceptions/{id}/decisions` or any other write/mutating
  endpoint, directly or indirectly. The three tools in section 4 are
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

## 7. Phased implementation roadmap

### Phase 0 — Current state (already true today)

CLI + Docker Compose + FastAPI + Postgres + Power BI + read-only MCP tools,
operated by a technical user. Includes a working (synchronous) file-upload
API endpoint (`POST /close-runs`), auto-seeded chart of accounts, and an
idempotent `demo` CLI command. This is the baseline this document builds on.

### Phase 1A — Run/job persistence, run-level data isolation, and atomic deterministic pipeline

**Narrow goal:** make the backend able to execute one full close review —
import through close-run snapshot — as a single durable, atomic,
import-batch-scoped operation, triggered by a job record. No browser UI,
no CSV-error polish, no auth: this phase is entirely about the pipeline
being correct and crash-safe before anything is built on top of it.

**Changed components:** the `close_jobs` table and job-orchestration node
(sections 2.3–2.4, 2.11); the `import_batch_id` schema change and its
uniqueness-key change on `journal_entries`/`bank_transactions`, and the
new `import_batch_id` column on `close_runs` (section 3); the
single-transaction-or-staged-cleanup mechanism spanning nodes 7–10
(section 2, node 7/10).

**Definition of Done:**
1. A job can be created and its status polled through
   `queued → running → succeeded`/`failed` (nodes 3, 4, 11, 12), driven by
   the existing upload endpoint extended to return a `job_id` immediately
   rather than blocking for the full run.
2. Uploading the same two files twice produces two independent
   `close_runs` rows, each with its own intact `import_batch_id`-scoped
   `journal_entries`/`bank_transactions` rows — neither run's rows are
   overwritten or mutated by the other, verified by a test that uploads
   twice and asserts both batches' rows still exist unchanged.
3. A test that forces a failure at each of node 8, node 9, and node 10 in
   turn asserts that **zero** rows remain under that job's
   `import_batch_id` in `journal_entries`, `journal_lines`,
   `bank_transactions`, `close_exceptions`, `close_run_exceptions`, and
   `close_runs` after the job is marked `failed`.
4. For a successful run, the resulting `close_run_id`'s exception data is
   identical in shape and content to what today's synchronous
   `run_close`/`POST /close-runs` produces for the same input files — this
   phase changes durability and isolation, not the accounting output.

### Phase 1B — Minimal browser upload, job status, and in-app results view

**Narrow goal:** give a non-technical user a URL-based way to trigger
Phase 1A's job pipeline and see its result, with no terminal, Docker, or
Power BI step in the path.

**Changed components:** node 1 (upload form, no period field), node 2's
close-period inference and the conditional month-picker (section 2, node
1/2), nodes 12–13 (job-status polling and results display).

**Definition of Done:**
1. Using only a browser pointed at the running app, a user selects two
   valid CSVs whose dates fall within one calendar month, clicks one
   button, and — without ever seeing or filling in a period field — is
   shown a results page once the job reaches `succeeded`.
2. Using the same flow with a journal file and a bank file whose dates
   span two different calendar months, the browser shows a month-choice
   control before a job is created; submitting a choice creates the job
   with that explicit `close_period` and proceeds to a results page.
3. After a run completes, closing the browser tab and reopening the same
   bookmarked job URL shows the identical results without re-uploading or
   re-running anything.
4. Loading a job URL with an unknown `job_id` shows a specific "not
   found" page; loading a `failed` job's URL shows its `error_detail` in
   plain language, in both cases without a blank page, a generic 500, or a
   raw stack trace.

### Phase 1C — Human-friendly CSV validation and new deterministic controls

**Narrow goal:** replace every raw Python/DB exception in the import path
with a structured, human-readable error, and add the two new deterministic
controls the v2 design requires.

**Changed components:** node 5 (CSV parsing & row-level validation), node
6 (reference-data check), node 8's two proposed additions (missing-
required-data control, date/account-validation control).

**Definition of Done:**
1. Uploading a journal CSV missing a required column (e.g. `account_code`)
   produces one structured error naming the missing column — not an
   uncaught `KeyError`.
2. Uploading a file with multiple bad rows (e.g. two rows with
   non-numeric `debit`) returns all of them together in one response, each
   identified by row number and offending value — not just the first, and
   not an uncaught `decimal.InvalidOperation`.
3. Uploading a journal line referencing an account code absent from
   `accounts` produces a specific message naming the account code and
   journal ID (e.g. "account code `9999` on journal `JE-2001` is not in
   the chart of accounts") — not a raw `psycopg` integrity error.
4. The missing-required-data control and the date/account-validation
   control each have an automated test proving they surface as
   *reviewable exceptions* (`close_exceptions` rows), not job failures,
   for a case that node 6's pre-import gate does not already catch.

### Phase 1D — Deployment and authentication (separate from the local product workflow)

**Narrow goal:** make the app reachable over a network by a non-technical
end user, as a deployment concern layered on top of Phases 1A–1C, not
mixed into their scope.

**Changed components:** hosting/deployment configuration; basic
authentication/access control (pulled forward from the previous Phase 2
plan, since it is a prerequisite for any *non-`localhost`* non-technical
user, per section 1's product goal).

**Definition of Done:**
1. The app is reachable via a URL from a machine other than the one
   running Docker/Postgres, with Docker/Postgres/FastAPI invisible to the
   end user (no setup step exposes them).
2. An automated test hitting the deployed instance's upload endpoint with
   no credentials receives a 401/redirect, not a processed upload.
3. Every Definition-of-Done check from Phases 1A–1C still passes when run
   against the authenticated, network-hosted deployment, not only against
   a local, unauthenticated instance.

### Phase 2 — Operational hardening

- Extend duplicate-reference detection to check against prior periods'
  data already in Postgres, not just the current import batch.
- Make the reconciliation cash account configurable instead of hardcoded
  to `1000`.
- Add explicit decision-transition rules to the existing decisions
  endpoint (e.g. reject `dismiss` on an already-`resolved` exception, or
  make such transitions an explicit, logged override).

### Phase 3 — Read-only AI investigation

- Implement the three tools in section 4 against the existing schema,
  including the `close_run_id`-scoped joins required by section 3.
- Add the "Investigate" action in the results UI (section 2.14), wired to
  produce the structured output in section 5.
- Add investigation logging (tool calls, evidence, output) as its own
  durable record, satisfying the auditability requirement in section 6.
- Everything in section 6's boundary list is enforced and, where feasible,
  covered by an automated test (e.g. a test asserting the AI tool module
  contains no `INSERT`/`UPDATE`/`DELETE` and no call to the decisions
  endpoint).

### Explicitly not scheduled in this roadmap

RAG, a vector database, autonomous multi-step agents, multi-agent
orchestration, and LangGraph are not part of Phase 1, 2, or 3. Introducing
any of them requires a new design document that re-examines the
auditability and "AI never decides accounting facts" guarantees this
document establishes.

## 8. Definition of Done

For Phases 1A–1D (the upload-and-run workflow) collectively, in addition
to each phase's own Definition of Done above:

1. Against a running local deployment, an end-to-end browser-driven test
   (no shell command issued after the deployment is started) selects two
   valid single-calendar-month CSVs, submits the upload form, and observes
   the job reach `succeeded` and the results page render — asserted by the
   test, not just visually confirmed.
2. Given a `succeeded` job's URL, a second, later HTTP GET to that same
   URL returns the same summary/exception counts as the first, and an
   assertion against `close_jobs` confirms no second job row was created
   between the two requests.
3. For each of {balance, duplicates, reconciliation, missing-required-data,
   date/account validation}, an automated unit test exercises that control
   in isolation and asserts its output on a fixed input/expected-output
   pair; a CI-run static check (e.g. a grep or import-linter rule) asserts
   zero references to an LLM/model-API client library anywhere in
   `close_review.py` or the control modules it calls.
4. For each of {missing required column, non-numeric debit/credit, unknown
   account code, blank required field}, an automated test submits a file
   with exactly that one defect and asserts the response is a 4xx with a
   specific `reason` naming the defect (e.g. `"missing column: account_code"`)
   — the test fails if the response is a 5xx or an unhandled exception
   propagates out of the request.
5. An automated test drives a job through `queued` → `running` →
   `succeeded` and, separately, `queued` → `running` → `failed`, then
   re-queries `close_jobs` after the request completes and asserts
   `status`, `error_detail` (on the failed case), and `updated_at` are all
   present and correct — i.e. retrievable after the fact, not only
   observable mid-request.
6. An automated test calls the existing `/close-summary` endpoint and the
   in-app results endpoint for the same `close_run_id` and asserts the two
   responses' exception counts and severity breakdowns are identical,
   with no Power BI process running during the test.
7. The project's test runner reports zero failures, and the test suite
   contains tests named/identifiable as covering: job creation, at least
   one row-level validation failure (per item 4), and the unknown-account-
   code failure — each a distinct, separately-asserting test, not folded
   into an unrelated test.
8. A CI-run, repository-wide search for AI/model-API client imports (e.g.
   `anthropic`, `openai`, or equivalent) across every module reachable
   from the deterministic pipeline (nodes 5–10) returns zero matches.

Additional checks that apply once Phase 3 (AI investigation) ships:

9. Each of the three read-only AI tools has automated tests covering,
   independently: a found result (assert the exact returned fields), a
   not-found result (assert `found: false` and no exception raised), and
   an invalid-input/error result (assert a typed error is raised, not a
   found/not-found response).
10. An automated test replays a stored investigation's `evidence_used`
    (same tool, same arguments) and asserts it returns byte-identical
    underlying rows; a separate automated test asserts that running an
    investigation on a fixture exception does not change that exception's
    `close_exceptions.status`, and that only a call to
    `/exceptions/{id}/decisions` changes it.

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
  operate the hosted instance (a technical concern addressed by Phase 1D's
  hosting/auth step, not by removing infrastructure entirely).
- Phases 1A–1D assume small-to-moderate CSV sizes (consistent with the
  project's current synthetic sample data) — this document does not
  address large-file streaming or long-running-job scaling; if job runtimes
  become long, `close_jobs` already supports moving from an in-process
  background task to a real task queue without changing the node contract.
- The single-calendar-month period-inference rule (section 2, node 2)
  assumes the common case where one upload represents one month's close.
  A file that legitimately spans multiple months on purpose (not by
  mistake) still works — it just always requires the explicit picker,
  since this design has no way to distinguish "intentional multi-month
  upload" from "wrong file selected" other than asking.