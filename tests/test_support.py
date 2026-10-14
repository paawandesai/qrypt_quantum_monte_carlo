import json

import pytest

from qmc import log, metrics, runtime
from qmc.store import from_dynamo, to_dynamo


def test_log_drops_fields_not_on_allow_list(capsys):
    log.info(event="x", job_id="j", authorization="Bearer secret", body={"S0": 1}, headers={"a": 1})
    rec = json.loads(capsys.readouterr().out)
    assert rec == {"level": "INFO", "event": "x", "job_id": "j"}


def test_emf_metric_has_only_environment_and_function_dimensions(capsys, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("FUNCTION_ROLE", "api")
    metrics.emit(JobsSubmitted=1, EntropyKiBConsumed=1)
    doc = json.loads(capsys.readouterr().out)
    cw = doc["_aws"]["CloudWatchMetrics"][0]
    assert cw["Namespace"] == "QryptMC"
    assert cw["Dimensions"] == [["Environment", "Function"]]
    assert {m["Name"] for m in cw["Metrics"]} == {"JobsSubmitted", "EntropyKiBConsumed"}
    assert doc["Environment"] == "dev" and doc["Function"] == "api" and doc["JobsSubmitted"] == 1
    assert "JobId" not in json.dumps(doc)


def test_unknown_metric_rejected():
    with pytest.raises(ValueError):
        metrics.emit(JobId=1)


def test_fingerprints():
    exp = runtime.expected_fingerprint()
    w = runtime.worker_fingerprint()
    assert set(exp) == {"numpy_version", "arch", "algo_version"}
    assert runtime.fingerprint_mismatch(exp, w) == []
    assert runtime.fingerprint_mismatch(exp, dict(w, arch="other")) == ["arch"]
    assert len(w["cpu_features_sha256"]) == 16


def test_build_info_file_is_used(tmp_path, monkeypatch):
    f = tmp_path / "build_info.json"
    f.write_text(json.dumps({"numpy_version": "9.9.9", "arch": "arm64"}))
    monkeypatch.setattr(runtime, "BUILD_INFO_FILE", f)
    runtime.build_info.cache_clear()
    try:
        assert runtime.expected_fingerprint()["numpy_version"] == "9.9.9"
    finally:
        runtime.build_info.cache_clear()


def test_dynamo_round_trip_is_exact():
    vals = {"a": 0.1, "b": 100.0, "c": [1.5e-300, 2], "d": True, "e": "s"}
    back = from_dynamo(to_dynamo(vals))
    assert back["a"] == 0.1 and back["b"] == 100 and back["c"] == [1.5e-300, 2]
    assert back["d"] is True and back["e"] == "s"
    assert from_dynamo({3, 1}) == [1, 3]
