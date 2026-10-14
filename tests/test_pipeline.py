"""End-to-end through the handlers against moto: submit -> workers -> aggregate -> replay."""

import json
from types import SimpleNamespace

import pytest
from conftest import drain_queue, fn_url_event
from fakes.mock_qrypt import MockQrypt
from test_api import make_app

from qmc import ids
from qmc.handlers import reconciler, worker
from qmc.store import Store

CTX = SimpleNamespace(aws_request_id="req-1")
STD = {"S0": 100, "K": 100, "r": 0.05, "sigma": 0.2, "T": 1, "paths": 200_000, "shards": 5}


@pytest.fixture
def env(aws, monkeypatch):
    store = Store()
    monkeypatch.setattr(worker, "_STORE", store)
    return {**aws, "store": store}


def submit(app, body=STD, path="/jobs"):
    resp = app.handle(fn_url_event("POST", path, body=body), CTX)
    return resp["statusCode"], json.loads(resp["body"])


def get(app, job_id):
    return json.loads(app.handle(fn_url_event("GET", f"/jobs/{job_id}"), CTX)["body"])


def run_workers(env, records=None):
    records = records if records is not None else drain_queue(env["sqs"], env["queue_url"])
    return worker.handler({"Records": records}, None), records


def test_job_runs_to_complete_with_ci(env):
    app = make_app()
    _, body = submit(app)
    out, records = run_workers(env)
    assert out == {"batchItemFailures": []} and len(records) == 5
    job = get(app, body["job_id"])
    assert job["status"] == "COMPLETE"
    res = job["result"]
    assert res["n"] == 200_000
    assert float(res["ci_low"]) < float(res["estimate"]) < float(res["ci_high"])
    assert abs(float(res["z_vs_black_scholes"])) < 4  # sanity only; the 3-SE test is in test_blackscholes
    assert job["progress"]["done"] == 5
    assert job["runtime_consistent"] is True


def test_duplicate_delivery_does_not_double_count(env):
    app = make_app()
    _, body = submit(app)
    records = drain_queue(env["sqs"], env["queue_url"])
    # deliver shard 0 twice before the rest, then everything again afterwards
    run_workers(env, [records[0], records[0]])
    run_workers(env, records)
    run_workers(env, records)
    job = get(app, body["job_id"])
    assert job["status"] == "COMPLETE" and job["result"]["n"] == 200_000
    shards = env["store"].get_shards(body["job_id"])
    assert sorted(s["shard_index"] for s in shards) == [0, 1, 2, 3, 4]

    # same answer as a clean single delivery of the same seed (replay)
    _status, rep = submit(app, body=None, path=f"/jobs/{body['job_id']}/replay")
    run_workers(env)
    assert get(app, rep["job_id"])["replay_comparison"]["verdict"] == "bit-identical"


def test_replay_is_bit_identical_and_uses_no_entropy(env):
    mock = MockQrypt()
    app = make_app(entropy=mock)
    _, body = submit(app)
    run_workers(env)
    original = get(app, body["job_id"])
    status, rep = submit(app, body=None, path=f"/jobs/{body['job_id']}/replay")
    assert status == 202 and rep["replay_of"] == body["job_id"] and rep["entropy_used_kib"] == 0
    assert mock.calls == 1  # no new Qrypt call for the replay
    run_workers(env)
    replay = get(app, rep["job_id"])
    assert replay["status"] == "COMPLETE"
    assert replay["master_seed_hex"] == original["master_seed_hex"]
    assert replay["entropy"] == original["entropy"]
    for f in ("estimate", "std_error", "ci_low", "ci_high"):
        assert replay["result"][f"{f}_hex"] == original["result"][f"{f}_hex"]
    assert replay["replay_comparison"]["verdict"] == "bit-identical"
    # the replay recomputed: it has its own shard items
    assert len(env["store"].get_shards(rep["job_id"])) == 5


def test_replay_refused_on_runtime_mismatch(env):
    app = make_app()
    _, body = submit(app)
    run_workers(env)
    env["store"].jobs.update_item(
        Key={"job_id": body["job_id"]},
        UpdateExpression="SET runtime_expected.numpy_version = :v",
        ExpressionAttributeValues={":v": "1.0.0"},
    )
    status, rep = submit(app, body=None, path=f"/jobs/{body['job_id']}/replay")
    assert status == 409 and rep["error"] == "runtime_mismatch" and rep["fields"] == ["numpy_version"]


def test_failing_shard_ends_partial_never_complete(env, monkeypatch):
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    app = make_app(env_extra={"ENABLE_FAULT_INJECTION": "true"})
    status, body = submit(app, body=dict(STD, fail_shards=[2]))
    assert status == 202
    records = drain_queue(env["sqs"], env["queue_url"])
    bad = next(r for r in records if json.loads(r["body"])["shard_index"] == 2)
    good = [r for r in records if r is not bad]
    run_workers(env, good)
    for attempt in (1, 2):
        out, _ = run_workers(env, [dict(bad, attributes={"ApproximateReceiveCount": str(attempt)})])
        assert out["batchItemFailures"] == [{"itemIdentifier": bad["messageId"]}]
        assert get(app, body["job_id"])["status"] == "RUNNING"
    out, _ = run_workers(env, [dict(bad, attributes={"ApproximateReceiveCount": "3"})])
    assert out["batchItemFailures"]  # returned as failed -> SQS moves it to the DLQ
    job = get(app, body["job_id"])
    assert job["status"] == "PARTIAL"
    assert job["failed_shard_indexes"] == [2]
    assert job["shard_errors"] == {"2": "InjectedFault"}
    assert "result" not in job


def test_fault_flag_ignored_when_injection_disabled(env, monkeypatch):
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "false")
    app = make_app()
    _, body = submit(app)
    records = drain_queue(env["sqs"], env["queue_url"])
    for r in records:
        msg = json.loads(r["body"])
        msg["fault"] = True
        r["body"] = json.dumps(msg)
    out, _ = run_workers(env, records)
    assert out["batchItemFailures"] == []
    assert get(app, body["job_id"])["status"] == "COMPLETE"


def test_reconciler_marks_overdue_job_partial(env):
    app = make_app()
    _, body = submit(app)
    records = drain_queue(env["sqs"], env["queue_url"])
    run_workers(env, records[:3])  # two shards never run (e.g. worker timeouts)
    assert reconciler.run(env["store"])["checked"] == 0  # not overdue yet
    past = ids.iso(ids.plus(ids.now(), -60))
    env["store"].jobs.update_item(
        Key={"job_id": body["job_id"]}, UpdateExpression="SET deadline_at = :d", ExpressionAttributeValues={":d": past}
    )
    out = reconciler.run(env["store"])
    assert out == {"checked": 1, "outcomes": {"PARTIAL": 1}}
    job = get(app, body["job_id"])
    assert job["status"] == "PARTIAL"
    assert job["failed_shard_indexes"] == sorted({0, 1, 2, 3, 4} - set(job["progress"]["done_indexes"]))


def test_reconciler_reaggregates_stale_aggregating_job(env):
    app = make_app()
    _, body = submit(app)
    run_workers(env)
    jid = body["job_id"]
    long_ago = ids.iso(ids.plus(ids.now(), -3600))
    env["store"].jobs.update_item(
        Key={"job_id": jid},
        UpdateExpression="SET #s = :a, updated_at = :t, deadline_at = :t REMOVE #r",
        ExpressionAttributeNames={"#s": "status", "#r": "result"},
        ExpressionAttributeValues={":a": "AGGREGATING", ":t": long_ago},
    )
    assert reconciler.run(env["store"])["outcomes"] == {"COMPLETE": 1}
    assert get(app, jid)["status"] == "COMPLETE"


def test_reconciler_handler_entrypoint(env, monkeypatch):
    monkeypatch.setattr(reconciler, "_STORE", None)
    assert reconciler.handler({}, None) == {"checked": 0, "outcomes": {}}


def test_bundle_contains_what_verify_needs(env):
    app = make_app()
    _, body = submit(app)
    run_workers(env)
    resp = app.handle(fn_url_event("GET", f"/jobs/{body['job_id']}/bundle"), CTX)
    b = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and "attachment" in resp["headers"]["Content-Disposition"]
    assert {"params", "master_seed_hex", "entropy", "result", "runtime_expected"} <= set(b)


def test_bad_message_is_reported_failed(env):
    out, _ = run_workers(env, [{"messageId": "m1", "body": "not json", "attributes": {}}])
    assert out == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
