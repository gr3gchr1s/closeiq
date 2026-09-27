from __future__ import annotations

from pathlib import Path
from typing import Any

from .close_jobs import create_close_job, run_close_job


def run_close(
    journal_file: str | Path,
    bank_file: str | Path,
    *,
    close_period: str,
    journal_source: str | None = None,
    bank_source: str | None = None,
) -> dict[str, Any]:
    """Run one close review synchronously, atomically, and run-isolated.

    Public entry point kept for existing callers (the CLI and the
    ``POST /close-runs`` API endpoint): same signature and return shape as
    before. Internally this now creates a durable close_jobs record first
    and executes the deterministic pipeline as a single atomic transaction
    scoped to that job's import batch (see close_jobs.run_close_job and
    docs/closeiq_v2_architecture.md, section 7, Phase 1A).
    """
    job_id = create_close_job(
        close_period,
        journal_source=journal_source or str(journal_file),
        bank_source=bank_source or str(bank_file),
    )

    return run_close_job(job_id, journal_file, bank_file)
