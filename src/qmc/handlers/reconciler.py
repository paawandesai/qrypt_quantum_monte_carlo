"""Reconciler Lambda, run on a schedule (every 5 minutes).

Finds jobs still active after their deadline and finalizes them: missing
shards (worker timeouts, crashes) are recorded as failed and the job becomes
PARTIAL; a job whose aggregator died mid-way is re-aggregated. It never reads
the dead-letter queue, so the DLQ depth alarm still fires.
"""

from __future__ import annotations

from .. import ids, log
from ..jobs import finalize
from ..store import Store

_STORE: Store | None = None


def run(store: Store) -> dict:
    jobs = store.active_jobs_past_deadline(ids.iso(ids.now()))
    outcomes: dict[str, int] = {}
    for job in jobs:
        try:
            status = finalize(store, job["job_id"], reconcile=True) or "unchanged"
        except Exception as exc:
            log.error(event="reconcile_error", job_id=job["job_id"], code=type(exc).__name__)
            status = "error"
        outcomes[status] = outcomes.get(status, 0) + 1
    log.info(event="reconcile", count=len(jobs), code=str(outcomes))
    if outcomes.get("error"):
        # Fail the invocation after trying every job, so Lambda's Errors metric
        # (and the reconciler-errors alarm) sees a job it could not finalize.
        raise RuntimeError(f"{outcomes['error']} job(s) could not be reconciled")
    return {"checked": len(jobs), "outcomes": outcomes}


def handler(event, context):
    global _STORE
    if _STORE is None:
        _STORE = Store()
    return run(_STORE)
