#!/usr/bin/env python3
"""Recompute a job from its bundle, independently of the deployed API.

  python scripts/qmc_cli.py bundle JOB_ID > bundle.json
  python scripts/verify.py bundle.json

Re-derives every shard seed from the stored master seed, re-simulates every
shard with the committed code, aggregates in shard order, and compares each
float64 hex value with the bundle. It also prints the Black-Scholes distance.

Bit-identical agreement is expected only on the same numpy version and CPU
architecture the job ran on (recorded in the bundle); on a different machine,
numpy may pick different SIMD kernels for exp() and the last bits can differ.
The script reports which, rather than hiding it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from qmc.aggregate import aggregate, compare_hex
from qmc.blackscholes import call_price
from qmc.jobs import params_from_record
from qmc.runtime import fingerprint_mismatch, worker_fingerprint
from qmc.seed import check_master_seed
from qmc.sim import simulate_shard


def main(path: str) -> int:
    b = json.loads(Path(path).read_text())
    params = params_from_record(b)
    master = b["master_seed_hex"]
    check_master_seed(master)
    parts = [simulate_shard(params, master, i, n) for i, n in enumerate(params.shard_sizes())]
    res = aggregate(parts, params)
    bs = call_price(params.S0, params.K, params.r, params.sigma, params.T)

    recorded = {f: (b.get("result") or {}).get(f"{f}_hex") for f in res.HEX_FIELDS}
    cmp = compare_hex(recorded, res.hex())
    here = worker_fingerprint()
    mismatch = fingerprint_mismatch(b.get("runtime_expected") or {}, here)

    print(f"job {b['job_id']}  paths={params.paths} shards={params.shards}")
    print(f"recomputed estimate {res.estimate!r}  SE {res.std_error!r}")
    print(f"95% CI [{res.ci_low!r}, {res.ci_high!r}]")
    print(f"Black-Scholes {bs!r}  z = {(res.estimate - bs) / res.std_error:+.3f}")
    for f, row in cmp["fields"].items():
        print(
            f"  {f:10s} recorded {row['original']}  recomputed {row['replay']}  {'equal' if row['equal'] else 'DIFF'}"
        )
    print(f"verdict: {cmp['verdict']}")
    if mismatch:
        print(
            f"note: this machine differs from the job's runtime in {mismatch} "
            f"(here: numpy {here['numpy_version']} on {here['arch']}); last-bit differences are possible."
        )
    return 0 if cmp["verdict"] == "bit-identical" else 1


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    if not Path(sys.argv[1]).is_file():
        sys.exit(f"no such bundle file: {sys.argv[1]}")
    sys.exit(main(sys.argv[1]))
