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


def test_replays_are_capped_per_day(env, monkeypatch):
    app = make_app(env_extra={"REPLAY_PER_DAY": "2"})
    _, body = submit(app, body=dict(STD, paths=1000, shards=1))
    run_workers(env)
    path = f"/jobs/{body['job_id']}/replay"
    assert [submit(app, body=None, path=path)[0] for _ in range(2)] == [202, 202]

    resp = app.handle(fn_url_event("POST", path), CTX)
    rep = json.loads(resp["body"])
    assert resp["statusCode"] == 429 and rep["error"] == "replay_limit_reached" and rep["job_created"] is False
    retry_after = int(resp["headers"]["Retry-After"])
    assert 0 < retry_after <= 86400 and rep["retry_after_s"] == retry_after
    listed = json.loads(app.handle(fn_url_event("GET", "/jobs"), CTX)["body"])
    assert len(listed["jobs"]) == 3  # the original and two replays; the refused one created nothing

    tomorrow = ids.plus(ids.now(), 86400)
    monkeypatch.setattr(ids, "now", lambda: tomorrow)
    assert submit(app, body=None, path=path)[0] == 202


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


def test_failing_shard_ends_partial_never_complete(env, monkeypatch, capsys):
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    app = make_app(env_extra={"ENABLE_FAULT_INJECTION": "true"})
    status, body = submit(app, body=dict(STD, fail_shards=[2]))
    assert status == 202
    records = drain_queue(env["sqs"], env["queue_url"])
    bad = next(r for r in records if json.loads(r["body"])["shard_index"] == 2)
    good = [r for r in records if r is not bad]
    run_workers(env, good)
    for attempt in (1, 2, 3, 4):
        out, _ = run_workers(env, [dict(bad, attributes={"ApproximateReceiveCount": str(attempt)})])
        assert out["batchItemFailures"] == [{"itemIdentifier": bad["messageId"]}]
        assert get(app, body["job_id"])["status"] == "RUNNING"
    out, _ = run_workers(env, [dict(bad, attributes={"ApproximateReceiveCount": "5"})])
    assert out["batchItemFailures"]  # returned as failed -> SQS moves it to the DLQ
    job = get(app, body["job_id"])
    assert job["status"] == "PARTIAL"
    assert job["failed_shard_indexes"] == [2]
    assert job["shard_errors"] == {"2": "InjectedFault"}
    assert "result" not in job
    # every failed attempt counts for the worker-errors alarm; only the last for shard-failures
    logs = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert sum(1 for doc in logs if doc.get("WorkerErrors") == 1) == 5
    assert sum(1 for doc in logs if doc.get("ShardFailures") == 1) == 1


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


def test_verify_script_recomputes_bundle(env, tmp_path, capsys):
    import importlib.util
    from pathlib import Path

    app = make_app()
    _, body = submit(app)
    run_workers(env)
    bundle = app.handle(fn_url_event("GET", f"/jobs/{body['job_id']}/bundle"), CTX)["body"]
    f = tmp_path / "bundle.json"
    f.write_text(bundle)
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("verify", root / "scripts" / "verify.py")
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)
    assert verify.main(str(f)) == 0
    assert "verdict: bit-identical" in capsys.readouterr().out


def test_failed_shard_is_released_after_backoff_not_full_visibility(env, monkeypatch):
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    app = make_app(env_extra={"ENABLE_FAULT_INJECTION": "true"})
    _, body = submit(app, body=dict(STD, shards=1, paths=1000, fail_shards=[0]))
    records = drain_queue(env["sqs"], env["queue_url"])
    calls = []
    real = env["store"].sqs.change_message_visibility

    def spy(**kw):
        calls.append(kw)
        return real(**kw)

    monkeypatch.setattr(env["store"].sqs, "change_message_visibility", spy)
    for attempt in (1, 2, 3, 4, 5):
        rec = dict(records[0], receiptHandle="h", attributes={"ApproximateReceiveCount": str(attempt)})
        run_workers(env, [rec])
    assert [c["VisibilityTimeout"] for c in calls] == [10, 20, 30, 40, 50]
    assert all(c["QueueUrl"] == env["queue_url"] for c in calls)
    assert get(app, body["job_id"])["status"] == "PARTIAL"


def test_backoff_failure_does_not_change_the_outcome(env, monkeypatch):
    def boom(**kw):
        raise RuntimeError("sqs down")

    monkeypatch.setattr(env["store"].sqs, "change_message_visibility", boom)
    rec = {"messageId": "m1", "receiptHandle": "h", "body": "not json", "attributes": {"ApproximateReceiveCount": "1"}}
    out, _ = run_workers(env, [rec])
    assert out["batchItemFailures"] == [{"itemIdentifier": "m1"}]


def test_retry_delay_is_linear_and_bounded(monkeypatch):
    monkeypatch.setenv("RETRY_BACKOFF_S", "10")
    assert [worker.retry_delay_s(i) for i in (1, 2, 5)] == [10, 20, 50]
    monkeypatch.setenv("RETRY_BACKOFF_S", "100000")
    assert worker.retry_delay_s(1) == 43_200


def test_malformed_message_is_counted_and_retried(env, capsys):
    records = [
        {"messageId": "m1", "body": "not json", "attributes": {"ApproximateReceiveCount": "1"}},
        {"messageId": "m2", "body": json.dumps({"job_id": "x"}), "attributes": {"ApproximateReceiveCount": "1"}},
    ]
    result, _ = run_workers(env, records)
    assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}, {"itemIdentifier": "m2"}]}
    out = capsys.readouterr().out
    assert out.count('"WorkerErrors": 1') == 2 and out.count('"bad_message"') == 2


def test_shard_write_is_conditional_so_a_redelivery_is_a_no_op(env, capsys):
    """Overwriting with identical data would hide a missing condition, so check
    the conditional put itself: the second write is refused and counted once."""
    app = make_app()
    _, body = submit(app, body=dict(STD, paths=1000, shards=1))
    records = drain_queue(env["sqs"], env["queue_url"])
    capsys.readouterr()
    run_workers(env, records)
    run_workers(env, records)
    out = capsys.readouterr().out
    assert out.count('"PathsSimulated": 1000') == 1
    assert '"code": "written"' in out and '"code": "duplicate"' in out

    store = env["store"]
    item = {"job_id": body["job_id"], "shard_index": 0, "n": 1}
    assert store.put_shard(item) is False  # already written by the worker


def test_queue_error_mid_enqueue_marks_job_partial_not_orphaned(env, monkeypatch):
    from botocore.exceptions import ClientError

    app = make_app()
    real = app.store.sqs.send_message_batch
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] > 1:
            raise ClientError({"Error": {"Code": "ThrottlingException"}}, "SendMessageBatch")
        return real(**kw)

    monkeypatch.setattr(app.store.sqs, "send_message_batch", flaky)
    status, body = submit(app, body=dict(STD, paths=20_000, shards=20))
    assert status == 202
    run_workers(env)
    job = get(app, body["job_id"])
    assert job["status"] == "PARTIAL"
    assert job["failed_shard_indexes"] == list(range(10, 20))


def test_reconciler_error_fails_the_invocation_after_trying_every_job(env, monkeypatch):
    app = make_app()
    _, a = submit(app)
    _, b = submit(app, body=dict(STD, paths=200_002))
    past = ids.iso(ids.plus(ids.now(), -60))
    for jid in (a["job_id"], b["job_id"]):
        env["store"].jobs.update_item(
            Key={"job_id": jid}, UpdateExpression="SET deadline_at = :d", ExpressionAttributeValues={":d": past}
        )
    seen = []
    real = reconciler.finalize

    def flaky(store, job_id, **kw):
        seen.append(job_id)
        if job_id == a["job_id"]:
            raise RuntimeError("AccessDenied")
        return real(store, job_id, **kw)

    monkeypatch.setattr(reconciler, "finalize", flaky)
    with pytest.raises(RuntimeError, match="1 job"):
        reconciler.run(env["store"])
    assert sorted(seen) == sorted([a["job_id"], b["job_id"]])  # the other job was still finalized
    assert get(app, b["job_id"])["status"] == "PARTIAL"


def test_network_error_mid_enqueue_marks_job_partial_and_counts_an_api_error(env, monkeypatch, capsys):
    from botocore.exceptions import EndpointConnectionError

    app = make_app()
    real = app.store.sqs.send_message_batch
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] > 1:
            raise EndpointConnectionError(endpoint_url="https://sqs.example")
        return real(**kw)

    monkeypatch.setattr(app.store.sqs, "send_message_batch", flaky)
    capsys.readouterr()
    status, body = submit(app, body=dict(STD, paths=20_000, shards=20))
    assert status == 202
    out = capsys.readouterr().out
    assert any('"ApiErrors": 1' in line for line in out.splitlines())
    run_workers(env)
    job = get(app, body["job_id"])
    assert job["status"] == "PARTIAL"
    assert job["failed_shard_indexes"] == list(range(10, 20))
