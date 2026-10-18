"""Job lifecycle: create, enqueue, finalize. Shared by api, worker and reconciler.

Status machine
  QUEUED -> RUNNING -> AGGREGATING -> COMPLETE
                    \\-> PARTIAL  (some shard failed or never finished)

Finalization is idempotent and safe under duplicate delivery:
- done_shards / failed_shards are DynamoDB number sets, so ADD twice is a no-op;
- exactly one caller wins the conditional flip to AGGREGATING and writes the
  result; everyone else sees the condition fail and does nothing.
"""

from __future__ import annotations

from . import ids, log, metrics
from .aggregate import AggregationError, aggregate, compare_hex
from .blackscholes import call_price
from .params import JobParams
from .runtime import expected_fingerprint, fingerprint_mismatch
from .sim import ShardPartial
from .store import ACTIVE_STATUSES, TTL_DAYS, Conflict, Store

# After this the reconciler marks unfinished jobs PARTIAL. It must exceed the
# time a shard can spend being retried (max receives x visibility timeout), or a
# shard still on its last retry would be written off. Terraform passes the real
# value as JOB_DEADLINE_S; this default matches 5 receives x 180 s + 15 min.
DEADLINE_S = 30 * 60
STALE_AGGREGATING_S = 120


def params_from_record(job: dict) -> JobParams:
    p = job["params"]
    return JobParams(
        S0=float(p["S0"]),
        K=float(p["K"]),
        r=float(p["r"]),
        sigma=float(p["sigma"]),
        T=float(p["T"]),
        paths=int(p["paths"]),
        shards=int(p["shards"]),
    )


def new_job_record(
    *,
    job_id: str,
    params: JobParams,
    master_seed_hex: str,
    entropy: dict,
    created,
    fail_shards: list[int] | None = None,
    replay_of: str | None = None,
    deadline_s: int = DEADLINE_S,
) -> dict:
    created_iso = ids.iso(created)
    rec = {
        "job_id": job_id,
        "status": "QUEUED",
        "created_at": created_iso,
        "updated_at": created_iso,
        "deadline_at": ids.iso(ids.plus(created, deadline_s)),
        "expires_at": ids.epoch_s(ids.plus(created, TTL_DAYS * 86400)),
        "params": params.as_dict(),
        "master_seed_hex": master_seed_hex,
        "entropy_source": entropy["source"],
        "entropy": entropy,
        "runtime_expected": expected_fingerprint(),
        "shard_errors": {},
    }
    if fail_shards:
        rec["fault_shards"] = fail_shards
    if replay_of:
        rec["replay_of"] = replay_of
    return rec


def shard_messages(job: dict) -> list[dict]:
    params = params_from_record(job)
    return [
        {
            "job_id": job["job_id"],
            "shard_index": i,
            "n_paths": n,
            "master_seed_hex": job["master_seed_hex"],
            "params": params.as_dict(),
            "fault": i in set(job.get("fault_shards", [])),
        }
        for i, n in enumerate(params.shard_sizes())
    ]


def create_and_enqueue(store: Store, make_record, id_parts: tuple[str, ...]) -> dict:
    """Put a job with a derived id (retrying on the unlikely collision), then enqueue."""
    for attempt in range(5):
        job_id = ids.derive_job_id(*id_parts, str(attempt))
        rec = make_record(job_id)
        try:
            store.put_new_job(rec)
            break
        except Conflict:
            continue
    else:  # pragma: no cover - five SHA-256 collisions in a row
        raise RuntimeError("could not allocate a job id")

    failed = store.enqueue_shards(shard_messages(rec))
    if failed:
        # Shards that never reached the queue can never complete: say so now.
        for i in failed:
            store.mark_shard_failed(rec["job_id"], i, "enqueue_failed")
        log.error(event="enqueue_failed", job_id=rec["job_id"], count=len(failed))
        metrics.emit(ApiErrors=1)  # the job will end PARTIAL; the api-errors alarm should see it
        finalize(store, rec["job_id"])
        rec = store.get_job(rec["job_id"]) or rec
    return rec


def finalize(store: Store, job_id: str, *, reconcile: bool = False) -> str | None:
    """Move a job to COMPLETE or PARTIAL if all shards are accounted for.

    With reconcile=True (the scheduled reconciler, after the deadline), missing
    shards count as failed, and a stale AGGREGATING job is re-aggregated.
    Returns the final status written by this call, or None.
    """
    job = store.get_job(job_id)
    if not job or job["status"] not in ACTIVE_STATUSES:
        return None
    params = params_from_record(job)
    done = set(job.get("done_shards", []))
    failed = set(job.get("failed_shards", [])) - done
    all_idx = set(range(params.shards))
    missing = all_idx - done - failed

    if missing and not reconcile:
        return None
    if reconcile:
        failed |= missing

    from_states: tuple[str, ...] = ("QUEUED", "RUNNING")
    if reconcile and job["status"] == "AGGREGATING":
        updated = job.get("updated_at", job["created_at"])
        if updated > ids.iso(ids.plus(ids.now(), -STALE_AGGREGATING_S)):
            return None
        from_states = ("AGGREGATING",)
    if not store.transition(job_id, from_states, "AGGREGATING"):
        return None  # someone else is finalizing

    if failed:
        store.transition(
            job_id,
            ("AGGREGATING",),
            "PARTIAL",
            {"failed_shard_indexes": sorted(failed), "completed_at": ids.iso(ids.now())},
        )
        log.warn(event="job_partial", job_id=job_id, count=len(failed))
        return "PARTIAL"

    shard_items = store.get_shards(job_id)
    partials = [ShardPartial.from_hex(s) for s in shard_items]
    try:
        result = aggregate(partials, params)
    except AggregationError:
        store.transition(
            job_id,
            ("AGGREGATING",),
            "PARTIAL",
            {
                "failed_shard_indexes": sorted(all_idx - {p.shard_index for p in partials}),
                "completed_at": ids.iso(ids.now()),
                "aggregation_error": "partials_incomplete",
            },
        )
        return "PARTIAL"

    bs = call_price(params.S0, params.K, params.r, params.sigma, params.T)
    record = result.to_record()
    record["black_scholes"] = repr(bs)
    record["z_vs_black_scholes"] = repr((result.estimate - bs) / result.std_error) if result.std_error > 0 else None

    extra = {"result": record, "completed_at": ids.iso(ids.now())}
    extra.update(runtime_summary(job, shard_items))
    if job.get("replay_of"):
        original = store.get_job(job["replay_of"])
        orig_hex = {f: (original or {}).get("result", {}).get(f"{f}_hex") for f in result.HEX_FIELDS}
        extra["replay_comparison"] = compare_hex(orig_hex, result.hex())
    store.transition(job_id, ("AGGREGATING",), "COMPLETE", extra)
    log.info(event="job_complete", job_id=job_id, paths=params.paths)
    return "COMPLETE"


def runtime_summary(job: dict, shard_items: list[dict]) -> dict:
    """Did every shard run on the fingerprint the job expected?"""
    expected = job.get("runtime_expected", {})
    seen = {}
    for s in shard_items:
        rt = s.get("runtime", {})
        key = (rt.get("numpy_version"), rt.get("arch"), rt.get("algo_version"), rt.get("cpu_features_sha256"))
        seen[key] = rt
    mismatches = sorted({f for rt in seen.values() for f in fingerprint_mismatch(expected, rt)})
    return {
        "runtime_observed": [
            {k: rt.get(k) for k in ("numpy_version", "arch", "algo_version", "cpu_features_sha256")}
            for rt in seen.values()
        ],
        "runtime_consistent": not mismatches and len({k[3] for k in seen}) <= 1,
        "runtime_mismatch_fields": mismatches,
    }
