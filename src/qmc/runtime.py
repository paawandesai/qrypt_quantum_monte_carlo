"""Runtime fingerprint recorded on every job and shard.

Replay is only claimed bit-identical when it runs the same numpy build, CPU
architecture and algorithm version. numpy picks SIMD kernels for exp() at
runtime from the CPU's features, so those are recorded per shard as well;
a mismatch is reported, never hidden.
"""

from __future__ import annotations

import hashlib
import json
import platform
from functools import cache
from pathlib import Path

from .sim import ALGO_VERSION

BUILD_INFO_FILE = Path(__file__).resolve().parent.parent / "build_info.json"


@cache
def build_info() -> dict:
    """What the deployed package was built with (written by scripts/build.py).

    Falls back to the local interpreter when running tests from a checkout.
    """
    if BUILD_INFO_FILE.exists():
        info = json.loads(BUILD_INFO_FILE.read_text())
    else:
        try:
            import numpy

            numpy_version = numpy.__version__
        except ImportError:  # pragma: no cover - api package has no numpy
            numpy_version = None
        info = {"numpy_version": numpy_version, "arch": _arch(), "source": "local"}
    info["algo_version"] = ALGO_VERSION
    return info


def _arch() -> str:
    m = platform.machine().lower()
    return {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}.get(m, m)


def expected_fingerprint() -> dict:
    """The (numpy, arch, algo) triple a replay must match."""
    bi = build_info()
    return {
        "numpy_version": bi.get("numpy_version"),
        "arch": bi.get("arch"),
        "algo_version": bi["algo_version"],
    }


@cache
def worker_fingerprint() -> dict:
    """Actual runtime of this worker container (needs numpy)."""
    import numpy

    features = {}
    try:
        from numpy._core._multiarray_umath import __cpu_features__

        features = {k: bool(v) for k, v in sorted(__cpu_features__.items())}
    except ImportError:  # pragma: no cover - numpy internals moved
        features = {}
    enabled = sorted(k for k, v in features.items() if v)
    return {
        "numpy_version": numpy.__version__,
        "arch": _arch(),
        "algo_version": ALGO_VERSION,
        "cpu_features": enabled,
        "cpu_features_sha256": hashlib.sha256(",".join(enabled).encode()).hexdigest()[:16],
    }


def fingerprint_mismatch(recorded: dict, current: dict) -> list[str]:
    """Names of fingerprint fields that differ (ignoring fields absent on either side)."""
    keys = ("numpy_version", "arch", "algo_version")
    return [k for k in keys if recorded.get(k) != current.get(k)]
