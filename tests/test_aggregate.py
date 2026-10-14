import math
import random

import pytest

from qmc.aggregate import Z_95, AggregationError, Result, aggregate, compare_hex
from qmc.params import JobParams
from qmc.seed import master_seed_from_block
from qmc.sim import CHUNK, ShardPartial, simulate_shard

P = JobParams(S0=100.0, K=100.0, r=0.05, sigma=0.2, T=1.0, paths=200_000, shards=8)
MASTER = master_seed_from_block(b"\x07" * 1024)


@pytest.fixture(scope="module")
def partials():
    return [simulate_shard(P, MASTER, i, n) for i, n in enumerate(P.shard_sizes())]


def test_order_of_completion_does_not_matter(partials):
    base = aggregate(partials, P)
    shuffled = list(partials)
    random.Random(1).shuffle(shuffled)
    assert aggregate(shuffled, P).hex() == base.hex()
    assert aggregate(list(reversed(partials)), P).hex() == base.hex()


def test_duplicate_partial_is_rejected_not_double_counted(partials):
    with pytest.raises(AggregationError):
        aggregate([*partials, partials[3]], P)


def test_missing_partial_rejected(partials):
    with pytest.raises(AggregationError):
        aggregate(partials[:-1], P)


def test_path_count_must_match(partials):
    bad = [*partials[:-1], ShardPartial(7, partials[-1].n + 1, partials[-1].sum, partials[-1].sum_sq)]
    with pytest.raises(AggregationError, match="paths"):
        aggregate(bad, P)


def test_needs_two_paths():
    p = JobParams(1, 1, 0, 1, 1, 1, 1)
    with pytest.raises(AggregationError):
        aggregate([ShardPartial(0, 1, 1.0, 1.0)], p)


def test_ci_is_estimate_plus_minus_z_se(partials):
    r = aggregate(partials, P)
    assert r.ci_low == r.estimate - Z_95 * r.std_error
    assert r.ci_high == r.estimate + Z_95 * r.std_error
    assert r.n == P.paths


def test_known_small_sample():
    # payoffs 1,2,3,4 in two shards; r=0 so no discount
    p = JobParams(1, 1, 0.0, 1, 1, 4, 2)
    r = aggregate([ShardPartial(1, 2, 7.0, 25.0), ShardPartial(0, 2, 3.0, 5.0)], p)
    assert r.estimate == 2.5
    assert r.std_error == math.sqrt((30 - 10 * 2.5) / 3 / 4)


def test_replay_recompute_is_bit_identical(partials):
    """Replay recomputes every shard from the seed; float64 hex must match exactly."""
    original = aggregate(partials, P)
    replayed = aggregate([simulate_shard(P, MASTER, i, n) for i, n in enumerate(P.shard_sizes())], P)
    assert replayed.hex() == original.hex()  # exact equality, no tolerance
    cmp = compare_hex(original.hex(), replayed.hex())
    assert cmp["verdict"] == "bit-identical"
    assert all(f["equal"] for f in cmp["fields"].values())


def test_compare_hex_flags_any_difference(partials):
    a = aggregate(partials, P).hex()
    b = dict(a, ci_high=(float.fromhex(a["ci_high"]) + 1e-15).hex())
    cmp = compare_hex(a, b)
    assert cmp["verdict"] == "different"
    assert cmp["fields"]["ci_high"]["equal"] is False
    assert compare_hex({}, a)["verdict"] == "different"


def test_partial_hex_round_trip(partials):
    for p in partials:
        assert ShardPartial.from_hex(p.to_hex()) == p


def test_result_record_has_hex_and_display(partials):
    rec = aggregate(partials, P).to_record()
    assert float.fromhex(rec["estimate_hex"]) == float(rec["estimate"])
    assert set(Result.HEX_FIELDS) <= {k[:-4] for k in rec if k.endswith("_hex")}


def test_chunking_spans_multiple_chunks():
    p = JobParams(100, 100, 0.05, 0.2, 1, CHUNK + 17, 1)
    part = simulate_shard(p, MASTER, 0, CHUNK + 17)
    assert part.n == CHUNK + 17 and part.sum > 0


def test_n_paths_must_be_positive():
    with pytest.raises(ValueError):
        simulate_shard(P, MASTER, 0, 0)
