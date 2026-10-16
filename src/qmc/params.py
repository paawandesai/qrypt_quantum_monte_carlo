"""Job parameter validation.

Pure functions: no AWS, no I/O. The api handler calls `validate_job_request`
on every submission; the same limits are exposed to the console through
GET /system so client-side hints match what the server enforces.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass

FLOAT_FIELDS = ("S0", "K", "r", "sigma", "T")
INT_FIELDS = ("paths", "shards")
OPTIONAL_FIELDS = ("fail_shards",)

# Sanity bounds that keep exp() far from overflow and the inputs meaningful.
BOUNDS = {
    "S0": (0.0, 1e6),  # exclusive low, inclusive high
    "K": (0.0, 1e6),
    "sigma": (0.0, 5.0),
    "T": (0.0, 50.0),
}
R_RANGE = (-1.0, 1.0)  # inclusive


class ValidationError(ValueError):
    """Raised with a list of human-readable problems; maps to HTTP 400."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Limits:
    max_paths: int = 10_000_000
    max_shards: int = 100
    max_paths_per_shard: int = 2_000_000
    fault_injection: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Limits:
        src: Mapping[str, str] = os.environ if env is None else env
        return cls(
            max_paths=int(src.get("MAX_PATHS", cls.max_paths)),
            max_shards=int(src.get("MAX_SHARDS", cls.max_shards)),
            max_paths_per_shard=int(src.get("MAX_PATHS_PER_SHARD", cls.max_paths_per_shard)),
            fault_injection=src.get("ENABLE_FAULT_INJECTION", "false").lower() == "true",
        )

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class JobParams:
    S0: float
    K: float
    r: float
    sigma: float
    T: float
    paths: int
    shards: int

    def as_dict(self) -> dict:
        return asdict(self)

    def shard_sizes(self) -> list[int]:
        return shard_sizes(self.paths, self.shards)


def shard_sizes(paths: int, shards: int) -> list[int]:
    """paths // shards per shard; the remainder goes to the lowest indexes."""
    base, extra = divmod(paths, shards)
    return [base + (1 if i < extra else 0) for i in range(shards)]


def _as_float(name: str, value, problems: list[str]) -> float | None:
    # bool is a subclass of int; reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(f"{name} must be a number")
        return None
    try:
        f = float(value)
    except OverflowError:  # an integer too large for a float64
        problems.append(f"{name} must be finite")
        return None
    if not math.isfinite(f):
        problems.append(f"{name} must be finite")
        return None
    return f


def _as_int(name: str, value, problems: list[str]) -> int | None:
    if isinstance(value, bool):
        problems.append(f"{name} must be an integer")
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        problems.append(f"{name} must be an integer")
        return None
    return value


def validate_job_request(body, limits: Limits) -> tuple[JobParams, list[int]]:
    """Validate a POST /jobs body. Returns (params, fail_shards).

    Raises ValidationError listing every problem found, not just the first.
    """
    if not isinstance(body, dict):
        raise ValidationError(["request body must be a JSON object"])

    problems: list[str] = []
    allowed = set(FLOAT_FIELDS) | set(INT_FIELDS) | set(OPTIONAL_FIELDS)
    unknown = sorted(set(body) - allowed)
    if unknown:
        problems.append(f"unknown field(s): {', '.join(unknown)}")
    missing = [f for f in (*FLOAT_FIELDS, *INT_FIELDS) if f not in body]
    if missing:
        problems.append(f"missing field(s): {', '.join(missing)}")
        raise ValidationError(problems)

    vals: dict = {}
    for name in FLOAT_FIELDS:
        vals[name] = _as_float(name, body[name], problems)
    for name in INT_FIELDS:
        vals[name] = _as_int(name, body[name], problems)

    for name, (lo, hi) in BOUNDS.items():
        v = vals[name]
        if v is not None and not (lo < v <= hi):
            problems.append(f"{name} must be > {lo:g} and <= {hi:g}")
    r = vals["r"]
    if r is not None and not (R_RANGE[0] <= r <= R_RANGE[1]):
        problems.append(f"r must be between {R_RANGE[0]:g} and {R_RANGE[1]:g}")

    paths, shards = vals["paths"], vals["shards"]
    if paths is not None:
        if paths < 1:
            problems.append("paths must be >= 1")
        elif paths > limits.max_paths:
            problems.append(f"paths must be <= {limits.max_paths} (cost limit)")
    if shards is not None:
        if shards < 1:
            problems.append("shards must be >= 1")
        elif shards > limits.max_shards:
            problems.append(f"shards must be <= {limits.max_shards}")
    if paths is not None and shards is not None and paths >= 1 and shards >= 1:
        if shards > paths:
            problems.append("shards must be <= paths")
        elif math.ceil(paths / shards) > limits.max_paths_per_shard:
            problems.append(
                f"paths per shard must be <= {limits.max_paths_per_shard} "
                "(keeps each worker well under its timeout); use more shards"
            )

    fail_shards: list[int] = []
    if "fail_shards" in body:
        raw = body["fail_shards"]
        if not limits.fault_injection:
            problems.append("fail_shards is only accepted when fault injection is enabled")
        elif not isinstance(raw, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in raw):
            problems.append("fail_shards must be a list of shard indexes")
        else:
            fail_shards = sorted(set(raw))
            if shards is not None and any(i < 0 or i >= shards for i in fail_shards):
                problems.append("fail_shards entries must be between 0 and shards-1")

    if problems:
        raise ValidationError(problems)
    return JobParams(**vals), fail_shards
