"""Aggregation of shard partials into a price, standard error and 95% CI.

Pure Python (no numpy) so the api, worker and reconciler can all run it.
Partials are sorted by shard_index and reduced with math.fsum in that fixed
order, so the result never depends on which shard finished first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .params import JobParams
from .sim import ShardPartial

Z_95 = 1.959963984540054  # two-sided 95% normal quantile


class AggregationError(ValueError):
    pass


@dataclass(frozen=True)
class Result:
    n: int
    estimate: float
    std_error: float
    ci_low: float
    ci_high: float

    HEX_FIELDS = ("estimate", "std_error", "ci_low", "ci_high")

    def hex(self) -> dict[str, str]:
        return {f: getattr(self, f).hex() for f in self.HEX_FIELDS}

    def to_record(self) -> dict:
        rec: dict[str, int | str] = {"n": self.n}
        for f in self.HEX_FIELDS:
            rec[f"{f}_hex"] = getattr(self, f).hex()
            rec[f] = repr(getattr(self, f))  # display; hex is authoritative
        return rec


def aggregate(partials: list[ShardPartial], params: JobParams) -> Result:
    expected = params.shards
    ordered = sorted(partials, key=lambda p: p.shard_index)
    indexes = [p.shard_index for p in ordered]
    if indexes != list(range(expected)):
        raise AggregationError(f"expected shard indexes 0..{expected - 1} exactly once, got {len(indexes)} partials")
    n = sum(p.n for p in ordered)
    if n != params.paths:
        raise AggregationError(f"partials cover {n} paths, job has {params.paths}")
    if n < 2:
        raise AggregationError("need at least 2 paths for a standard error")

    s = math.fsum(p.sum for p in ordered)
    ss = math.fsum(p.sum_sq for p in ordered)
    mean = s / n
    var = max((ss - s * mean) / (n - 1), 0.0)
    disc = math.exp(-params.r * params.T)
    estimate = disc * mean
    se = disc * math.sqrt(var / n)
    return Result(n, estimate, se, estimate - Z_95 * se, estimate + Z_95 * se)


def compare_hex(original: dict[str, str], replay: dict[str, str]) -> dict:
    """Field-by-field float64 hex comparison. 'bit-identical' only if all match."""
    rows = {
        f: {"original": original.get(f), "replay": replay.get(f), "equal": original.get(f) == replay.get(f)}
        for f in Result.HEX_FIELDS
    }
    identical = all(r["equal"] and r["original"] is not None for r in rows.values())
    return {"fields": rows, "verdict": "bit-identical" if identical else "different"}
