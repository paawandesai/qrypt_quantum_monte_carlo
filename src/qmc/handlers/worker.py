"""Worker Lambda: SQS consumer, one shard per message (batch size 1).

Writes {sum, sum_sq, n} for its shard with a conditional put, so a duplicate
delivery never double-counts. On the last allowed receive a failure is recorded
on the job (so it ends PARTIAL, never COMPLETE with a wrong total) and the
message is returned as failed, which sends it to the dead-letter queue.
"""

from __future__ import annotations

import json
import os
import time

from .. import ids, log, metrics
from ..jobs import finalize
from ..params import JobParams
from ..runtime import worker_fingerprint
from ..sim import simulate_shard
from ..store import TTL_DAYS, Store


class InjectedFault(RuntimeError):
    """Raised for shards named in fail_shards when fault injection is enabled."""


_STORE: Store | None = None


def _store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store()
    return _STORE


def max_receives() -> int:
    return int(os.environ.get("MAX_RECEIVE_COUNT", "3"))


def all_accounted(job: dict) -> bool:
    seen = set(job.get("done_shards", [])) | set(job.get("failed_shards", []))
    return len(seen) >= int(job["params"]["shards"])


def process(store: Store, msg: dict, receive_count: int) -> None:
    job_id = msg["job_id"]
    i = int(msg["shard_index"])
    started = ids.now()
    t0 = time.perf_counter()
    try:
        if msg.get("fault") and os.environ.get("ENABLE_FAULT_INJECTION", "false") == "true":
            raise InjectedFault(f"injected failure for shard {i}")
        params = JobParams(**msg["params"])
        partial = simulate_shard(params, msg["master_seed_hex"], i, int(msg["n_paths"]))
        item = {
            "job_id": job_id,
            **partial.to_hex(),
            "sum": repr(partial.sum),
            "runtime": worker_fingerprint(),
            "started_at": ids.iso(started),
            "finished_at": ids.iso(ids.now()),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "attempt": receive_count,
            "expires_at": ids.epoch_s(ids.plus(started, TTL_DAYS * 86400)),
        }
        written = store.put_shard(item)
        if written:
            metrics.emit(PathsSimulated=partial.n)
        job = store.mark_shard_done(job_id, i)
        if job["status"] == "QUEUED":
            store.transition(job_id, ("QUEUED",), "RUNNING")
        log.info(
            event="shard_done",
            job_id=job_id,
            shard_index=i,
            attempt=receive_count,
            duration_ms=item["duration_ms"],
            code="written" if written else "duplicate",
        )
        if all_accounted(job):
            finalize(store, job_id)
    except Exception as exc:
        code = type(exc).__name__
        log.error(event="shard_error", job_id=job_id, shard_index=i, attempt=receive_count, code=code)
        if receive_count >= max_receives():
            metrics.emit(ShardFailures=1)
            job = store.mark_shard_failed(job_id, i, code)
            if all_accounted(job):
                finalize(store, job_id)
        raise


def handler(event, context):
    store = _store()
    failures = []
    for record in event.get("Records", []):
        receive_count = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
        try:
            msg = json.loads(record["body"])
            process(store, msg, receive_count)
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
