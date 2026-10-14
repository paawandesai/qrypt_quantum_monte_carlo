"""Shard simulation: European call under Black-Scholes (GBM terminal price).

Each shard draws standard normals from PCG64 seeded by its shard SeedSequence,
in fixed chunks, and returns undiscounted payoff sums. The chunk size and the
reduction order are part of ALGO_VERSION because they change float rounding.
Discounting happens once, in aggregation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .params import JobParams
from .seed import shard_seed_sequence

CHUNK = 262_144
ALGO_VERSION = "eu-call-gbm/v1 pcg64 standard_normal chunk=262144 np.sum+fsum"


@dataclass(frozen=True)
class ShardPartial:
    shard_index: int
    n: int
    sum: float  # sum of undiscounted payoffs max(S_T - K, 0)
    sum_sq: float  # sum of squared undiscounted payoffs

    def to_hex(self) -> dict:
        return {
            "shard_index": self.shard_index,
            "n": self.n,
            "sum_hex": self.sum.hex(),
            "sum_sq_hex": self.sum_sq.hex(),
        }

    @classmethod
    def from_hex(cls, d: dict) -> ShardPartial:
        return cls(
            shard_index=int(d["shard_index"]),
            n=int(d["n"]),
            sum=float.fromhex(d["sum_hex"]),
            sum_sq=float.fromhex(d["sum_sq_hex"]),
        )


def simulate_shard(params: JobParams, master_hex: str, shard_index: int, n_paths: int) -> ShardPartial:
    import numpy as np

    if n_paths < 1:
        raise ValueError("n_paths must be >= 1")
    rng = np.random.Generator(np.random.PCG64(shard_seed_sequence(master_hex, shard_index)))
    drift = (params.r - 0.5 * params.sigma * params.sigma) * params.T
    vol = params.sigma * math.sqrt(params.T)

    sums: list[float] = []
    sqs: list[float] = []
    remaining = n_paths
    while remaining > 0:
        m = min(CHUNK, remaining)
        z = rng.standard_normal(m)
        st = params.S0 * np.exp(drift + vol * z)
        pay = np.maximum(st - params.K, 0.0)
        sums.append(float(pay.sum()))
        sqs.append(float((pay * pay).sum()))
        remaining -= m
    return ShardPartial(shard_index, n_paths, math.fsum(sums), math.fsum(sqs))
