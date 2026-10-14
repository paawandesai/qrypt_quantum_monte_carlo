"""Correctness against the closed form (PRD case S0=100, K=100, r=0.05, sigma=0.2, T=1).

Two separate tests, by design:

1. test_fixed_seed_within_3_se: deterministic. Its seed is the master seed of
   an all-zero 1 KiB block, chosen before the test was first run and never
   changed. Same code + same numpy -> same answer every time.

2. test_fresh_seed_within_3_se (marked `statistical`): draws a fresh seed from
   OS entropy on every run, so it really tests the estimator. If the code is
   correct, |z| > 3 happens with probability ~0.27%, so this test is EXPECTED
   to fail about 1 run in 370. Its tolerance and seed must never be changed to
   make a run pass; a failure prints the seed so it can be reproduced.
"""

import math

import numpy as np
import pytest

from qmc.aggregate import aggregate
from qmc.blackscholes import call_price
from qmc.params import JobParams
from qmc.seed import master_seed_from_block
from qmc.sim import simulate_shard

PARAMS = JobParams(S0=100.0, K=100.0, r=0.05, sigma=0.2, T=1.0, paths=1_000_000, shards=10)
BS = 10.450583572185565


def run(master_hex, params=PARAMS):
    parts = [simulate_shard(params, master_hex, i, n) for i, n in enumerate(params.shard_sizes())]
    return aggregate(parts, params)


def test_closed_form_reference_value():
    assert call_price(100, 100, 0.05, 0.2, 1) == pytest.approx(10.4506, abs=5e-5)
    assert call_price(100, 100, 0.05, 0.2, 1) == BS


def test_closed_form_limits():
    # deep in the money -> S0 - K e^{-rT}; deep out -> ~0
    assert call_price(200, 50, 0.05, 0.2, 1) == pytest.approx(200 - 50 * math.exp(-0.05), rel=1e-9)
    assert call_price(1, 1000, 0.05, 0.2, 1) < 1e-12
    with pytest.raises(ValueError):
        call_price(100, 100, 0.05, 0, 1)


def test_fixed_seed_within_3_se():
    master = master_seed_from_block(b"\x00" * 1024)
    a = run(master)
    b = run(master)
    assert a.hex() == b.hex()  # deterministic: identical float64 bits on rerun
    z = (a.estimate - BS) / a.std_error
    assert abs(z) < 3, f"z={z:.3f}"
    assert 0.0140 < a.std_error < 0.0155  # 3 SE ~ 0.044 at 1M paths


@pytest.mark.statistical
def test_fresh_seed_within_3_se():
    ss = np.random.SeedSequence()  # fresh OS entropy every run (test-only)
    master = f"{ss.entropy % (1 << 256):064x}"
    r = run(master)
    z = (r.estimate - BS) / r.std_error
    assert abs(z) < 3, (
        f"|z|={abs(z):.3f} >= 3 with master seed {master}. Expected about 0.27% of runs; "
        "re-run to confirm before suspecting the code. Do not widen the tolerance."
    )
