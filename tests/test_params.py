import pytest

from qmc.params import JobParams, Limits, ValidationError, shard_sizes, validate_job_request

STD = {"S0": 100, "K": 100, "r": 0.05, "sigma": 0.2, "T": 1, "paths": 1_000_000, "shards": 10}
LIM = Limits()


def test_standard_case_is_valid():
    p, fail = validate_job_request(dict(STD), LIM)
    assert p == JobParams(100.0, 100.0, 0.05, 0.2, 1.0, 1_000_000, 10)
    assert fail == []


@pytest.mark.parametrize(
    "field,value,needle",
    [
        ("sigma", 0, "sigma must be > 0"),
        ("sigma", -0.2, "sigma must be > 0"),
        ("T", 0, "T must be > 0"),
        ("T", -1, "T must be > 0"),
        ("S0", 0, "S0 must be > 0"),
        ("K", -5, "K must be > 0"),
        ("paths", 0, "paths must be >= 2"),
        ("paths", -10, "paths must be >= 2"),
        ("paths", 1, "paths must be >= 2"),
        ("r", 1e-131, "r must be 0 or at least"),
        ("S0", 1e-200, "S0 must be 0 or at least"),
        ("shards", 0, "shards must be >= 1"),
        ("sigma", "0.2", "sigma must be a number"),
        ("paths", "100", "paths must be an integer"),
        ("paths", 1.5, "paths must be an integer"),
        ("paths", True, "paths must be an integer"),
        ("S0", True, "S0 must be a number"),
        ("r", 2.0, "r must be between"),
        ("sigma", float("nan"), "sigma must be finite"),
        ("S0", float("inf"), "S0 must be finite"),
        ("paths", 10_000_001, "cost limit"),
        ("shards", 101, "shards must be <= 100"),
    ],
)
def test_invalid_values_rejected(field, value, needle):
    body = dict(STD, **{field: value})
    with pytest.raises(ValidationError) as ei:
        validate_job_request(body, LIM)
    assert any(needle in p for p in ei.value.problems), ei.value.problems


def test_shards_greater_than_paths_rejected():
    with pytest.raises(ValidationError, match="shards must be <= paths"):
        validate_job_request(dict(STD, paths=5, shards=10), LIM)


def test_oversized_paths_per_shard_rejected():
    with pytest.raises(ValidationError, match="paths per shard"):
        validate_job_request(dict(STD, paths=5_000_000, shards=2), LIM)


def test_integral_float_paths_accepted():
    p, _ = validate_job_request(dict(STD, paths=1000.0, shards=2.0), LIM)
    assert p.paths == 1000 and isinstance(p.paths, int)


def test_missing_and_unknown_fields():
    with pytest.raises(ValidationError) as ei:
        validate_job_request({"S0": 1, "extra": 2}, LIM)
    msg = str(ei.value)
    assert "unknown field(s): extra" in msg and "missing field(s)" in msg


def test_non_object_body():
    with pytest.raises(ValidationError, match="JSON object"):
        validate_job_request([1, 2], LIM)


def test_fail_shards_only_with_fault_injection():
    with pytest.raises(ValidationError, match="fault injection"):
        validate_job_request(dict(STD, fail_shards=[1]), LIM)
    lim = Limits(fault_injection=True)
    _, fail = validate_job_request(dict(STD, fail_shards=[3, 1, 3]), lim)
    assert fail == [1, 3]
    with pytest.raises(ValidationError, match="between 0 and shards-1"):
        validate_job_request(dict(STD, fail_shards=[10]), lim)
    with pytest.raises(ValidationError, match="list of shard indexes"):
        validate_job_request(dict(STD, fail_shards="1"), lim)


def test_shard_sizes_spread_remainder_to_lowest():
    assert shard_sizes(10, 3) == [4, 3, 3]
    assert sum(shard_sizes(1_000_003, 10)) == 1_000_003
    assert JobParams(1, 1, 0, 1, 1, 7, 7).shard_sizes() == [1] * 7


def test_limits_from_env():
    lim = Limits.from_env(
        {"MAX_PATHS": "5", "MAX_SHARDS": "2", "MAX_PATHS_PER_SHARD": "3", "ENABLE_FAULT_INJECTION": "TRUE"}
    )
    assert lim == Limits(5, 2, 3, True)
    assert lim.as_dict()["max_paths"] == 5
