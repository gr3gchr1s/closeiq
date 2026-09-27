# Phase 1B implementation spec: browser upload, job status, results view

Status: proposed spec, not yet implemented. Expands
`docs/closeiq_v2_architecture.md`, section 7 (Phase 1B), into an
implementation-ready breakdown: an explicit API contract, and a set of
workstreams scoped so they can be built independently (by separate
people, or separate agents) against a fixed contract, then integrated.

## 1. What Phase 1A actually left in place

Read before starting any workstream below — this is what already exists,
so nothing here should be re-derived or redesigned:

- `close_jobs` table + `close_jobs.py`: `create_close_job`,
  `get_close_job`, `run_close_job`. A job's full lifecycle
  (`queued → running → succeeded`/`failed`) is already durable and
  run-isolated (see `docs/closeiq_v2_architecture.md`, section 3).
- `run_close_job` is **synchronous** — it runs the entire deterministic
  pipeline in the calling thread/request and returns only when it's
  done. There is no background execution yet. This is the main backend
  gap Phase 1B closes (see section 2 below).
- `POST /close-runs` (`api.py`) creates a job (via `run_close`, which
  wraps `create_close_job` + `run_close_job`) and blocks for the whole
  run before responding with the full result, including `job_id`.
- `GET /close-runs/{close_run_id}/exceptions` and
  `GET /close-runs/{close_run_id}/close-summary` already return one
  run's data, 404 on an unknown run. `GET /exceptions` and
  `GET /close-summary` default to the latest run. All of this is
  reusable as-is by the results page (section 3.3).
- No period-inference logic exists yet: `POST /close-runs` still
  requires an explicit `close_period` form field.
- No frontend of any kind exists (`find` for `package.json`/`static`/
  `frontend` turns up nothing) — this is greenfield. `fastapi[standard]`
  already pulls in `jinja2` and `fastapi.staticfiles`, confirmed
  importable in `.venv`, so a plain server-rendered + vanilla-JS
  frontend needs no new dependency.

## 2. The one required backend behavior change: make the upload non-blocking

This is not optional — it's what makes "click Run, walk away, come back
later" (the product goal in section 1 of the architecture doc) possible.
Today's `POST /close-runs` cannot do this; it holds the HTTP connection
open for the full pipeline.

`POST /close-runs` must:
1. Validate the upload (existing filename/`.csv` checks).
2. Resolve the close period (new — section 3 below).
3. Create the job (`create_close_job`) and schedule `run_close_job` to
   execute **after** the response is sent, via FastAPI's
   `BackgroundTasks`. See section 2.1 immediately below for exactly what
   this does and does not give Phase 1B — it is a deliberately narrow
   mechanism, not a task queue.
4. Respond immediately (`202 Accepted`) with just
   `{"job_id": ..., "status": "queued"}`.
5. The uploaded file bytes must be written to a job-owned path *before*
   the response is sent (as today, but see section 3.4's storage
   contract for exactly where and how), and that path must survive until
   the background task reads it — `BackgroundTasks` runs after the
   response, but the request's `TemporaryDirectory` context manager
   would have already cleaned up by then, so the upload can no longer
   live in a `TemporaryDirectory` scoped to the request handler.

### 2.1 What `BackgroundTasks` is — and is explicitly not — for Phase 1B

`BackgroundTasks` runs the scheduled callable in the same process, after
the response is sent, on the same event loop's worker (via
`run_in_threadpool` for a sync callable like `run_close_job`). Stated
plainly, since this shapes both WS-B's design and this phase's honest
limits:

- **Phase 1B supports exactly one local, single-process API instance.**
  There is no cross-process or cross-instance job dispatch. Running two
  `uvicorn` workers, or the containerized `api` service scaled beyond
  one replica, is not supported by this mechanism and is out of scope
  for this phase.
- **It is not durable.** A scheduled background task lives only in that
  process's memory. If the process is killed, restarted, or crashes
  while a task is running (including a container restart), that task
  simply stops running mid-execution — nothing re-queues it, and nothing
  resumes it. This is a real, user-visible gap: a job can be left
  `running` forever with no process left to finish it.
- **Startup stale-job policy (required, small/local-scale only):** on
  process startup, the API must sweep `close_jobs` for any row still
  `status = 'running'` (these can only exist because the previous
  process died mid-job — no code path in this system otherwise leaves a
  job `running` when nothing is executing it) and mark each one `failed`
  with an `error_detail` such as `"Interrupted by an application
  restart before this run finished; please re-upload."` This is a
  correctness requirement, not a nice-to-have: without it, a reviewer
  polling a job orphaned by a restart waits forever with no feedback.
  This sweep is a simple, synchronous startup step (a FastAPI
  `startup` event or lifespan handler) — it is not a background worker
  and needs no new dependency. It is deliberately blunt (mark, don't try
  to resume) because Phase 1B's data is small and synthetic; this is not
  meant to survive real interrupted production work.
- **A separate worker/queue (e.g. a real task queue, multiple worker
  processes, or resumable jobs) is explicitly a later operational-
  hardening milestone** (see the architecture doc's Phase 2 and the
  "small-to-moderate CSV sizes" assumption at the end of that document),
  **not Phase 1B.** No workstream below should build toward one.

## 3. API contract

### 3.1 `POST /close-runs` (changed)

- **Request:** `journal_file`, `bank_file` (unchanged); `close_period`
  becomes **optional**. Omit it to request automatic inference.
- **Success (202):** `{"job_id": "<uuid>", "status": "queued"}`.
- **Period ambiguous (422):**
  `{"detail": {"reason": "period_ambiguous", "months": ["2026-03", "2026-04"]}}`
  — returned when the journal/bank files' dates span more than one
  calendar month. The client resubmits with an explicit `close_period`.
- **Unparseable dates (422):**
  `{"detail": {"reason": "unparseable_dates", "rows": [...]}}` — at
  least one date in either file couldn't be parsed during the
  lightweight pre-parse (full row-level validation is Phase 1C's job;
  this check only needs to be good enough to bound the inference).
- **Existing validation errors** (missing filename, wrong extension,
  malformed explicit `close_period`) are unchanged, still 422.

### 3.2 `GET /close-jobs/{job_id}` (new)

Thin wrapper around `close_jobs.get_close_job`. Returns:
```json
{
  "job_id": "...", "status": "queued|running|succeeded|failed",
  "close_period": "2026-08", "journal_source": "...", "bank_source": "...",
  "close_run_id": "..." ,
  "error_detail": null,
  "created_at": "...", "updated_at": "..."
}
```
404 with a clear `detail` if `job_id` is unknown.

### 3.3 Results, once `status == "succeeded"`

No new endpoints needed — the client calls the existing, already
run-scoped:
- `GET /close-runs/{close_run_id}/exceptions`
- `GET /close-runs/{close_run_id}/close-summary`

using the `close_run_id` from 3.2's response.

### 3.4 Upload storage lifecycle (required, not optional)

Today's `POST /close-runs` writes uploads into a request-scoped
`TemporaryDirectory` under fixed filenames (`journal_entries.csv`,
`bank_transactions.csv`) that it fully controls — it never touches the
caller-supplied filename as a path component. Moving to a background
task changes the lifetime but must preserve that safety property
exactly:

- **Location:** a job-owned directory named by `job_id` (which is
  already a server-generated UUID — see `close_jobs.create_close_job`),
  e.g. `<uploads_root>/<job_id>/`, where `<uploads_root>` is a directory
  **outside** anything mounted by WS-C's `StaticFiles` (section 4,
  WS-C) or any other publicly served path. Nothing under it is ever
  served directly to a browser.
- **Filenames:** the two files are written under fixed, server-chosen
  names (e.g. `journal.csv` / `bank.csv`) inside that job's directory —
  **never** the client-supplied `UploadFile.filename` (already true
  today; this must remain true when the code moves off
  `TemporaryDirectory`, since a hand-rolled path join is exactly the
  kind of change that reintroduces a path-traversal risk that
  `TemporaryDirectory` + fixed names previously prevented for free).
  The original filename is still recorded as `journal_source`/
  `bank_source` — that's data, stored in a column, never a filesystem
  path.
- **Cleanup on success:** the background task deletes the job's upload
  directory after `run_close_job` returns successfully (the data is
  durably in Postgres by then; the on-disk copy has no further purpose).
- **Cleanup on failure:** the background task deletes the job's upload
  directory in a `finally` (or equivalent) even when `run_close_job`
  raises — `run_close_job` already guarantees the database side rolls
  back cleanly (Phase 1A); the filesystem side must not leak a directory
  per failed run.
- **Startup cleanup for abandoned directories:** because a killed
  process (section 2.1) can leave a job's upload directory behind with
  no code left to clean it up, the same startup sweep that marks
  orphaned `running` jobs `failed` also removes any upload directory
  under `<uploads_root>` whose `job_id` is not `queued` or `running` in
  `close_jobs` (i.e. its job already finished, one way or another, and
  nothing should still be reading that directory) — or, more simply for
  this phase's small scale, any upload directory whose job is no longer
  `queued`/`running` gets removed unconditionally on startup, since
  Phase 1B never needs an upload directory after its one job either
  succeeds or fails.
- **Upload size limits: out of scope for Phase 1B**, unless a limit is
  already enforced somewhere in today's code (it is not — today's
  `create_close_run_from_upload` only checks filename presence and
  `.csv` extension, no size check). Do not add one in this phase; it
  belongs with Phase 1C's broader "malformed upload" validation work,
  where it can be given a real, considered limit and error message
  rather than an arbitrary one bolted on here.

## 4. Workstreams

Each workstream lists its file ownership and the *contract* (section 3)
it depends on — not the other workstream's implementation. Built against
the contract, they can proceed in parallel; integration is just wiring
real endpoints in place of the contract assumption.

### WS-A: Close-period inference (backend, independent)

- **New file:** `src/closeiq/period_inference.py`.
- **Function:** something like
  `infer_close_period(journal_path, bank_path) -> str`, raising a typed
  exception (e.g. `PeriodAmbiguousError(months: list[str])` /
  `UnparseableDatesError(rows: list[...])`) on the two failure cases in
  section 3.1.
  **CSV column name, verified against `data/*.csv` and
  `accounting.py`/`reconciliation.py`'s loaders — do not confuse with
  the database column names:** both `journal_entries.csv` and
  `bank_transactions.csv` (and their `demo_*` equivalents) use a column
  literally named `date` for this — *not* `journal_date` or
  `transaction_date`, which are `journal_entries.journal_date` and
  `bank_transactions.transaction_date` in the Postgres schema only
  (`database/schema.sql`), assigned from the CSV's `date` column at
  import time (`journal_import.py`, `bank_import.py`). This function
  pre-parses the CSV `date` column in both files only — it must not
  duplicate Phase 1C's full row validation.
- **Tests:** single-month journal+bank → returns that month;
  multi-month → raises with the correct distinct months; a bad date →
  raises the unparseable case; empty file → a clear error (reuse
  whatever convention Phase 1C settles on for "empty file", or a
  simple `ValueError` for now since Phase 1C isn't built yet).
- **No dependency on any other workstream.**

### WS-B: Async job creation + status endpoint (backend)

- **Owns `src/closeiq/api.py` exclusively for this phase** (see section
  6 — no other workstream edits this file except the single
  `StaticFiles` mount line, which integration adds at the end).
- **Changed/added in `api.py`:** `POST /close-runs` (per section 2,
  including the upload-storage handling in section 3.4, replacing its
  current `TemporaryDirectory` usage); new `GET /close-jobs/{job_id}`
  (section 3.2); a startup handler implementing section 2.1's stale-job
  sweep and section 3.4's abandoned-upload-directory cleanup.
- **Depends on WS-A's function signature/exceptions** (call it directly
  once WS-A lands; until then, stub it inline with a `# TODO: replace
  with period_inference.infer_close_period` so this workstream isn't
  blocked).
- **Tests:**
  - Job created + `202` + no `close_period` given + single-month files;
    `422` + `period_ambiguous` for multi-month files; explicit
    `close_period` still works exactly as before (backward compatible).
  - Unknown `job_id` → 404 at `GET /close-jobs/{job_id}`.
  - **Deterministic proof the POST schedules rather than calls inline:**
    patch/mock `run_close_job` (or the module-level reference to it used
    by the endpoint) and assert the `POST` handler returns its `202`
    response *without* that mock having been awaited/called yet — i.e.
    assert on call-order/call-count at the moment the response is
    produced, not on wall-clock timing. This is the test that actually
    proves "non-blocking," not a timing assertion (see section 8, item
    1, for why the previous version of this spec was wrong to rely on
    timing here).
  - **Deterministic proof polling can observe `queued`/`running` before
    `succeeded`:** use a controlled gate — e.g. monkeypatch
    `run_close_job` (or inject a hook it already calls, such as one of
    the import functions) to block on a `threading.Event` the test
    holds, so the test can assert `GET /close-jobs/{job_id}` returns
    `running` *while the gate is held*, then release the gate and assert
    it reaches `succeeded`. No `sleep`-based race.
  - **End-to-end polling test, retained as a coarser sanity check:** the
    real (unpatched) flow, polling `GET /close-jobs/{job_id}` in a loop
    with a short bounded timeout until it reaches a terminal status —
    this is allowed to use real timing, but only as a "did it eventually
    finish" check, never as the proof of asynchrony itself.
  - Startup sweep: a job left `running` (simulating a killed process,
    e.g. by inserting a `close_jobs` row with `status='running'` and no
    process actually running it) is marked `failed` with a specific
    `error_detail` after the startup handler runs; its upload directory
    (if present) is removed.

### WS-C: Upload + results frontend (new, independent of backend internals)

- **New files:** a `static/` (or `web/`) directory served via
  `fastapi.staticfiles.StaticFiles`, mounted in `api.py`; plain HTML +
  vanilla JS (no build step, no framework — consistent with "minimal
  web front end" in the architecture doc and the project's
  zero-extra-dependency ethos). Suggested pages:
  - `/` — upload form: two file inputs, one "Run Close Review" button,
    no period field visible by default.
  - `/jobs/{job_id}` — a static shell page whose JS polls
    `GET /close-jobs/{job_id}` (a sensible interval — 2s is plenty for
    this scale) and, once `succeeded`, calls `GET
    /close-runs/{close_run_id}/exceptions` and `.../close-summary` and
    renders them; on `failed`, renders `error_detail` in plain
    language; on 404, renders "job not found."
  - The month picker (section 3.1's `period_ambiguous` response) is a
    small inline `<select>` shown only on that specific 422, which then
    resubmits the same two files with `close_period` set.
- **Depends only on the contract in section 3**, not on WS-A/WS-B's
  actual code — build and manually verify against a hand-rolled mock
  server or `curl`-crafted fixture responses matching section 3's
  shapes, then re-verify against the real endpoints once WS-B lands.
- **Owns the new static asset files exclusively** (see section 6) —
  does **not** edit `api.py`; the single `StaticFiles` mount line is
  added by integration, not by this workstream, to avoid two
  workstreams editing the same file concurrently.
- **Required DOM-safety rule, no exceptions:** every value that
  originates from the API — `exception_type`, `evidence` fields,
  `source_ids`, `error_detail`, `journal_source`/`bank_source` (which
  echo the uploaded filenames), any CSV-derived string — is inserted
  into the page using a DOM text API (`el.textContent = value`, or
  equivalent, e.g. `document.createTextNode`), never string-concatenated
  into `innerHTML`. `evidence` in particular is a JSONB blob shaped by
  whatever a control put in it (`accounting.py`/`reconciliation.py`) —
  nothing about its contents is sanitized for HTML anywhere in the
  pipeline, so treating it as safe markup would be a stored-XSS path
  from a crafted CSV description/reference field straight into the
  reviewer's browser. Static markup that never includes API/CSV data
  (the page's own labels, layout) may use `innerHTML`/template literals
  as usual — this rule is about data provenance, not a blanket ban.
- **Test:** at least one test (browser-driven, e.g. via a headless
  check, or a DOM-level unit test if the JS is factored to allow one)
  asserting that an exception whose `evidence`/description contains
  `<script>`-like content renders as literal visible text, not as
  executed markup.

### WS-D: Error/empty-state coverage (small, can ride with WS-C or run separately)

- Specifically the two Definition-of-Done items that are easy to skip:
  a bookmarked `/jobs/{job_id}` URL for an **unknown** job shows a
  specific message (not a blank page); a **failed** job's URL shows
  `error_detail` in plain language, with no dead-end (e.g. a link back
  to `/` to re-upload).
- Depends on WS-B's 404/`error_detail` contract and WS-C's page
  structure existing; if resourcing allows, fold into WS-C directly
  instead of tracking separately.

## 5. Suggested integration order

1. Lock this contract (section 3) — nothing below should change it
   without updating this doc.
2. WS-A and WS-B can start immediately, in parallel, against the
   contract; WS-B stubs WS-A until it lands, then swaps in the real
   call.
3. WS-C starts immediately too, against the contract, independent of
   WS-A/WS-B's implementation.
4. Integrate: point WS-C at the real WS-B endpoints, confirm the
   `period_ambiguous` flow end-to-end with a genuinely multi-month
   sample file (not currently in `data/` — one will need to be added
   for this test, or a synthetic in-memory CSV built in the test).
5. WS-D verified last, since it needs both a real 404 and a real
   `failed` job to point at.

## 6. Agent execution boundaries

If these workstreams are executed by separate coding agents (or separate
people) at the same time, the following are required, not optional —
they are what make "built independently, integrated once" actually safe
rather than a source of silent conflicts:

- **One worktree/branch per workstream.** WS-A, WS-B, WS-C (and WS-D, if
  tracked separately) each get their own branch/worktree. Nobody commits
  directly to the integration branch mid-workstream.
- **No concurrent edits to `api.py`.** WS-B is the sole owner of
  `src/closeiq/api.py` for the duration of this phase. WS-A never
  touches it (it's a new, separate module). WS-C never touches it except
  for the one `StaticFiles` mount line, which it does not add itself —
  see below.
- **WS-C owns only the new static asset files** (its new `static/`/
  `web/` directory and its contents) — nothing in `src/closeiq/`.
- **Integration owns the `StaticFiles` mount.** The single line wiring
  WS-C's static directory into `api.py` is added once, by whoever
  performs integration (section 5), after both WS-B's and WS-C's
  branches are otherwise merged — not by WS-B or WS-C individually. This
  is the one point where the two workstreams' output touches the same
  file, so it is deliberately a named, sequenced step rather than left
  to whichever branch merges first.
- **Integration runs the full test suite after merging every
  workstream**, not just the tests each workstream wrote for itself —
  `python -m unittest discover -s tests -v` (per the README) must pass
  against the merged result before Phase 1B is considered done.

## 7. Explicitly out of scope for Phase 1B

Matches the architecture doc: Phase 1C's row-level CSV/account-code
error messages and the two new deterministic controls; Phase 1D's
deployment/auth; any AI investigation feature; any change to the
existing deterministic controls, `close_jobs`/`close_run_exceptions`
schema, or MCP tools; a durable worker/task queue (see section 2.1) and
upload size limits (see section 3.4), both deferred to later phases.

## 8. Definition of Done

Restates `docs/closeiq_v2_architecture.md` section 7 (Phase 1B) as
concrete, testable checks:

1. **Asynchrony is proven deterministically, not by timing.** A prior
   version of this spec proposed asserting "the `POST` response lands
   well before the job's `updated_at` shows `succeeded`" — that's a race
   disguised as a check: on a fast enough machine, or a trivially small
   fixture, a synchronous implementation could satisfy it too. The
   actual required checks (see WS-B's test list, section 4) are:
   - a test proving `POST /close-runs` returns its `202` before
     `run_close_job` has been invoked (call-order/mock-based, not
     timing-based);
   - a test using a controlled gate (e.g. a `threading.Event`) to
     deterministically observe `GET /close-jobs/{job_id}` return
     `queued` and then `running` while the gated job is held open,
     before it's allowed to reach `succeeded`;
   - an end-to-end polling test (real, unpatched flow) that polls until
     a terminal status within a bounded timeout, kept as a coarse
     sanity check that the whole path actually works — but never used as
     the proof of non-blocking behavior on its own.
2. Automated test: the same flow with journal/bank dates spanning two
   calendar months returns `422`/`period_ambiguous` with the correct
   distinct months; resubmitting with an explicit `close_period` then
   succeeds.
3. Manual or browser-driven check: from `/`, selecting two valid CSVs
   and clicking "Run Close Review" navigates to a `/jobs/{job_id}` page
   that shows the summary/exception list once the job succeeds, with no
   terminal/Docker/Power BI step involved.
4. Manual or browser-driven check: reloading a completed job's
   `/jobs/{job_id}` URL (simulating "closed the tab, came back later")
   shows the same result without re-uploading or re-running anything.
5. Automated test + manual check: an unknown `job_id` at
   `/close-jobs/{job_id}` (API) and `/jobs/{job_id}` (page) both give a
   specific "not found," never a blank page or a raw 500.
6. Automated test: a `close_jobs` row left `running` (simulating an
   interrupted process, per section 2.1) is marked `failed` with a
   specific `error_detail` after the startup sweep runs, and its upload
   directory (per section 3.4) no longer exists on disk afterward.
7. Automated test: a value containing `<script>`-like content in an
   exception's evidence/description renders as literal text on the
   results page, never as executed markup (per WS-C's DOM-safety rule,
   section 4).
