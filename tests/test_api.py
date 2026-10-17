import json
from types import SimpleNamespace

import pytest
from conftest import TEST_BEARER, TEST_ENV, FakeSecrets, fn_url_event
from fakes.mock_qrypt import MockQrypt

from qmc.handlers.api import App

STD = {"S0": 100, "K": 100, "r": 0.05, "sigma": 0.2, "T": 1, "paths": 100_000, "shards": 4}
CTX = SimpleNamespace(aws_request_id="req-1")


def make_app(entropy=None, env_extra=None, secrets=None):
    import os

    env = dict(os.environ)
    env.update(env_extra or {})
    return App(env=env, entropy=entropy or MockQrypt(), secrets=secrets or FakeSecrets())


def call(app, *a, **kw):
    resp = app.handle(fn_url_event(*a, **kw), CTX)
    return (
        resp["statusCode"],
        json.loads(resp["body"]) if resp["headers"]["Content-Type"].startswith("application/json") else resp["body"],
        resp["headers"],
    )


def test_empty_list_on_fresh_deployment(aws):
    status, body, headers = call(make_app(), "GET", "/jobs")
    assert status == 200 and body == {"jobs": [], "next_before": None}
    assert headers["X-Robots-Tag"].startswith("noindex")


def test_unknown_job_is_404_with_message(aws):
    status, body, _ = call(make_app(), "GET", "/jobs/" + "0" * 32)
    assert status == 404 and body["error"] == "job_not_found" and "0" * 32 in body["message"]


def test_malformed_job_id_is_400(aws):
    status, body, _ = call(make_app(), "GET", "/jobs/%23entropy-ledger")
    assert status == 400 and body["error"] == "invalid_job_id"


@pytest.mark.parametrize("key", [None, "wrong", ""])
def test_auth_required(aws, key):
    status, body, _ = call(make_app(), "GET", "/jobs", key=key)
    assert status == 401 and body["error"] == "unauthorized"


def test_api_key_unavailable_fails_closed(aws):
    status, body, _ = call(make_app(secrets=FakeSecrets(values={})), "GET", "/jobs")
    assert status == 503 and body["error"] == "api_key_unavailable"


def test_empty_stored_key_fails_closed(aws):
    app = make_app(secrets=FakeSecrets(values={TEST_ENV["API_KEY_PARAM"]: ""}))
    status, body, _ = call(app, "GET", "/jobs", key="")
    assert status == 503 and body["error"] == "api_key_unavailable"


def test_submit_creates_queued_job_with_seed_and_provenance(aws):
    mock = MockQrypt(request_id="qr-42")
    app = make_app(entropy=mock)
    status, body, _ = call(app, "POST", "/jobs", body=STD)
    assert status == 202 and body["status"] == "QUEUED" and mock.calls == 1
    status, job, _ = call(app, "GET", f"/jobs/{body['job_id']}")
    assert status == 200
    assert job["status"] == "QUEUED"
    assert len(job["master_seed_hex"]) == 64
    assert job["entropy_source"] == "qrypt"
    assert job["entropy"]["qrypt_request_id"] == "qr-42"
    assert job["entropy"]["endpoint_host"] == "mock.test"  # the host that served the block, not assumed
    assert len(job["entropy"]["block_sha256"]) == 64
    assert job["params"]["paths"] == 100_000
    assert job["progress"] == {"shards": 4, "done": 0, "failed": 0, "done_indexes": [], "failed_indexes": []}
    msgs = aws["sqs"].receive_message(QueueUrl=aws["queue_url"], MaxNumberOfMessages=10)["Messages"]
    assert sorted(json.loads(m["Body"])["shard_index"] for m in msgs) == [0, 1, 2, 3]
    _, listing, _ = call(app, "GET", "/jobs")
    assert [j["job_id"] for j in listing["jobs"]] == [body["job_id"]]


@pytest.mark.parametrize(
    "cause,http_status,expected",
    [
        ("qrypt_unauthorized", 401, 503),
        ("qrypt_allotment_exhausted", 403, 503),
        ("qrypt_rate_limited", 429, 503),
        ("qrypt_internal_error", 500, 503),
        ("qrypt_entropy_unavailable", 503, 503),
        ("qrypt_timeout", None, 504),
        ("qrypt_unreachable", None, 503),
        ("qrypt_key_unavailable", None, 503),
    ],
)
def test_qrypt_failure_rejects_job_and_creates_nothing(aws, cause, http_status, expected):
    app = make_app(entropy=MockQrypt(fail_with=cause, http_status=http_status))
    status, body, _ = call(app, "POST", "/jobs", body=STD)
    assert status == expected
    assert body["error"] == "entropy_unavailable"
    assert body["cause"] == cause and body["qrypt_status"] == http_status
    assert body["job_created"] is False
    _, listing, _ = call(app, "GET", "/jobs")
    assert listing["jobs"] == []
    assert "Messages" not in aws["sqs"].receive_message(QueueUrl=aws["queue_url"])


def test_invalid_input_is_400_with_problems(aws):
    app = make_app()
    status, body, _ = call(app, "POST", "/jobs", body=dict(STD, sigma=-1, paths="many"))
    assert status == 400 and body["error"] == "invalid_request"
    assert any("sigma" in p for p in body["problems"]) and any("paths" in p for p in body["problems"])
    status, body, _ = call(app, "POST", "/jobs", body="{not json")
    assert status == 400 and body["error"] == "invalid_json"


def test_cost_limit_rejected_before_entropy_is_spent(aws):
    mock = MockQrypt()
    status, _body, _ = call(make_app(entropy=mock), "POST", "/jobs", body=dict(STD, paths=50_000_000))
    assert status == 400 and mock.calls == 0


def test_oversized_body_rejected_before_parsing(aws):
    status, _body, _ = call(make_app(), "POST", "/jobs", body="x" * 5000)
    assert status == 413


def test_rate_limit_returns_429_with_retry_after(aws, monkeypatch):
    from datetime import UTC, datetime

    from qmc import ids

    fixed = datetime(2026, 10, 16, 12, 0, 3, tzinfo=UTC)  # 3 s into a window
    monkeypatch.setattr(ids, "now", lambda: fixed)
    mock = MockQrypt()
    app = make_app(entropy=mock, env_extra={"QRYPT_RATE_PER_10S": "2"})
    codes = [call(app, "POST", "/jobs", body=dict(STD, paths=1000, shards=1))[0] for _ in range(2)]
    status, body, headers = call(app, "POST", "/jobs", body=dict(STD, paths=1000, shards=1))
    assert codes == [202, 202]
    assert status == 429 and int(headers["Retry-After"]) == 7 and body["job_created"] is False
    assert mock.calls == 2  # the refused submit never reached Qrypt


def test_entropy_ceiling_blocks_submit(aws):
    mock = MockQrypt()
    app = make_app(entropy=mock, env_extra={"ENTROPY_CEILING_KIB": "2"})
    assert call(app, "POST", "/jobs", body=dict(STD, paths=1000, shards=1))[0] == 202
    assert call(app, "POST", "/jobs", body=dict(STD, paths=1000, shards=1))[0] == 202
    status, body, _ = call(app, "POST", "/jobs", body=dict(STD, paths=1000, shards=1))
    assert status == 409 and body["error"] == "entropy_ceiling_reached" and mock.calls == 2
    _, sysinfo, _ = call(app, "GET", "/system")
    assert sysinfo["entropy"] == {"kib_used": 2, "ceiling_kib": 2, "kib_per_job": 1}


def test_health_never_decrypts_or_calls_qrypt(aws):
    secrets = FakeSecrets()
    mock = MockQrypt()
    app = make_app(entropy=mock, secrets=secrets)
    status, body, _ = call(app, "GET", "/health")
    assert status == 200 and body["status"] == "ok"
    assert set(body["tables"].values()) == {"ACTIVE"}
    assert mock.calls == 0
    # the only secret read is the API key for auth; the Qrypt key is never touched
    assert TEST_ENV["QRYPT_KEY_PARAM"] not in secrets.loaded_at
    # SSM reachability is checked with a metadata-only call, never a decrypt
    assert body["ssm"] == "reachable" and secrets.reachable_calls == 1


def test_health_degraded_when_ssm_unreachable(aws):
    secrets = FakeSecrets()
    secrets.reachable = lambda name: False
    status, body, _ = call(make_app(secrets=secrets), "GET", "/health")
    assert status == 503 and body["status"] == "degraded" and body["ssm"] == "unreachable"


def test_secret_cache_strips_and_expires():
    from qmc.handlers.api import SecretCache

    values = iter(["key-one\n", "key-two\n", "metadata-only"])
    calls = []

    class Ssm:
        def get_parameter(self, Name, WithDecryption):
            calls.append(WithDecryption)
            return {"Parameter": {"Value": next(values)}}

    now = [0.0]
    cache = SecretCache(lambda: Ssm(), ttl_s=3600, clock=lambda: now[0])
    assert cache.get("p") == "key-one"  # trailing newline from a file never part of a key
    now[0] = 3599
    assert cache.get("p") == "key-one" and calls == [True]
    now[0] = 3600  # a rotated key is picked up within the TTL
    assert cache.get("p") == "key-two" and calls == [True, True]
    assert cache.reachable("p") and calls[-1] is False


def test_secret_cache_serves_stale_value_through_an_ssm_blip():
    from qmc.handlers.api import SecretCache

    up = [True]
    built = []

    class Ssm:
        def get_parameter(self, Name, WithDecryption):
            if not up[0]:
                raise RuntimeError("ssm down")
            return {"Parameter": {"Value": "key"}}

    def factory():
        built.append(1)
        return Ssm()

    now = [0.0]
    cache = SecretCache(factory, ttl_s=10, stale_s=100, clock=lambda: now[0])
    assert cache.get("p") == "key"
    up[0] = False
    now[0] = 50  # past the TTL, refresh fails: keep serving the cached key
    assert cache.get("p") == "key" and not cache.reachable("p")
    now[0] = 110  # past TTL + stale window: fail closed
    with pytest.raises(RuntimeError):
        cache.get("p")
    with pytest.raises(RuntimeError):
        SecretCache(factory).get("never-loaded")  # nothing cached: no fallback
    assert len(built) == 2  # one client per cache, not one per call


def test_head_on_console_is_200_without_body(aws):
    resp = make_app().handle(fn_url_event("HEAD", "/"), CTX)
    assert resp["statusCode"] == 200 and resp["body"] == ""


def test_huge_number_is_400_not_500(aws):
    status, body, _ = call(make_app(), "POST", "/jobs", body=dict(STD, S0=10**400))
    assert status == 400 and any("S0 must be finite" in p for p in body["problems"])


def test_system_reports_limits_and_dlq(aws):
    status, body, _ = call(make_app(), "GET", "/system")
    assert status == 200
    assert body["limits"]["max_paths"] == 10_000_000 and body["dlq_depth"] == 0
    assert "application-layer" in body["auth"]


def test_list_filters_and_validates(aws):
    app = make_app()
    call(app, "POST", "/jobs", body=dict(STD, paths=1000, shards=1))
    assert len(call(app, "GET", "/jobs", query={"status": "QUEUED"})[1]["jobs"]) == 1
    assert call(app, "GET", "/jobs", query={"status": "COMPLETE"})[1]["jobs"] == []
    assert call(app, "GET", "/jobs", query={"status": "nope"})[0] == 400
    assert call(app, "GET", "/jobs", query={"limit": "0"})[0] == 400
    assert call(app, "GET", "/jobs", query={"limit": "x"})[0] == 400


def test_unknown_route_404(aws):
    assert call(make_app(), "DELETE", "/jobs")[0] == 404


def test_replay_of_unfinished_job_refused(aws):
    app = make_app()
    _, body, _ = call(app, "POST", "/jobs", body=dict(STD, paths=1000, shards=1))
    status, r, _ = call(app, "POST", f"/jobs/{body['job_id']}/replay")
    assert status == 409 and r["error"] == "not_replayable"
    assert call(app, "POST", "/jobs/" + "1" * 32 + "/replay")[0] == 404


def test_logs_never_contain_auth_header_or_body(aws, capsys):
    app = make_app()
    call(app, "POST", "/jobs", body=dict(STD, paths=1000, shards=1))
    call(app, "GET", "/jobs", key="wrong-key-value-xyz")
    out = capsys.readouterr().out
    assert TEST_BEARER not in out and "wrong-key-value-xyz" not in out
    assert "authorization" not in out.lower() and "Bearer" not in out
    assert '"S0"' not in out


def test_unhandled_exception_is_500_without_detail(aws, monkeypatch, capsys):
    app = make_app()
    monkeypatch.setattr(app, "list_jobs", lambda e: 1 / 0)
    status, body, _ = call(app, "GET", "/jobs")
    assert status == 500 and body == {"error": "internal_error", "message": "unexpected server error"}
    out = capsys.readouterr().out
    assert "ZeroDivision" not in out
    assert '"ApiErrors": 1' in out  # counted for the api-errors alarm


def test_console_served_without_key_with_noindex_and_csp(aws):
    app = make_app()
    resp = app.handle(fn_url_event("GET", "/", key=None), CTX)
    assert resp["statusCode"] == 200 and resp["headers"]["Content-Type"].startswith("text/html")
    assert 'name="robots" content="noindex' in resp["body"] and "Demo — synthetic data only" in resp["body"]
    assert "script-src 'self'" in resp["headers"]["Content-Security-Policy"]
    assert resp["headers"]["X-Robots-Tag"].startswith("noindex")
    for path, ctype in (("/static/app.js", "text/javascript"), ("/static/app.css", "text/css")):
        r = app.handle(fn_url_event("GET", path, key=None), CTX)
        assert r["statusCode"] == 200 and r["headers"]["Content-Type"] == ctype


@pytest.mark.parametrize("path", ["/static/../handlers/api.py", "/static/nope.js", "/static/../../build_info.json"])
def test_static_cannot_escape_console_dir(aws, path):
    resp = make_app().handle(fn_url_event("GET", path, key=None), CTX)
    assert resp["statusCode"] == 404


def test_console_has_no_third_party_scripts():
    from pathlib import Path

    console = Path(__file__).resolve().parent.parent / "src" / "qmc" / "console"
    html = (console / "index.html").read_text()
    js = (console / "app.js").read_text()
    assert "http://" not in html and "https://" not in html
    assert "innerHTML" not in js and "localStorage" not in js


def test_job_deadline_comes_from_configuration(aws):
    from datetime import datetime

    app = make_app(env_extra={"JOB_DEADLINE_S": "1800"})
    _, body, _ = call(app, "POST", "/jobs", body=STD)
    _, job, _ = call(app, "GET", f"/jobs/{body['job_id']}")

    def ts(s):
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    assert (ts(job["deadline_at"]) - ts(job["created_at"])).total_seconds() == 1800
