"""Qrypt client: every failure maps to a named cause and raises; nothing falls back."""

import base64
import http.client
import io
import json
import urllib.error
import urllib.request

import pytest

from qmc import qrypt
from qmc.qrypt import QryptClient, QryptError, parse_response

BLOCK = bytes(range(256)) * 4


def ok_body(blocks=None):
    blocks = blocks if blocks is not None else [base64.b64encode(BLOCK).decode()]
    return json.dumps({"random": blocks, "size": len(blocks)}).encode()


class FakeResp(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


URL = "https://api-eus.qrypt.com/api/v1/quantum-entropy"


def client(monkeypatch, outcome, token="tok"):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.header_items())
        seen["timeout"] = timeout
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(qrypt, "_open", fake_urlopen)
    return QryptClient(lambda: token, url=URL, timeout_s=2), seen


def test_success_requests_one_kib_with_bearer(monkeypatch):
    c, seen = client(monkeypatch, FakeResp(ok_body(), {"X-Request-Id": "abc-123"}))
    blk = c.fetch_block()
    assert blk.data == BLOCK and blk.kib == 1 and blk.request_id == "abc-123"
    assert blk.endpoint_host == "api-eus.qrypt.com"
    assert seen["url"].endswith("?size=1")
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["headers"]["Accept"] == "application/json"
    assert seen["headers"]["User-agent"] == "qrypt-monte-carlo/0.1"
    assert "urllib" not in seen["headers"]["User-agent"].lower()
    assert seen["timeout"] == 2


def test_no_request_id_is_null_not_invented(monkeypatch):
    c, _ = client(monkeypatch, FakeResp(ok_body()))
    assert c.fetch_block().request_id is None


@pytest.mark.parametrize(
    "status,cause",
    [
        (400, "qrypt_bad_request"),
        (401, "qrypt_unauthorized"),
        (403, "qrypt_allotment_exhausted"),
        (429, "qrypt_rate_limited"),
        (500, "qrypt_internal_error"),
        (503, "qrypt_entropy_unavailable"),
        (502, "qrypt_http_error"),
    ],
)
def test_http_errors_raise_named_cause(monkeypatch, status, cause):
    err = urllib.error.HTTPError(URL, status, "x", {}, None)
    c, _ = client(monkeypatch, err)
    with pytest.raises(QryptError) as ei:
        c.fetch_block()
    assert ei.value.cause == cause and ei.value.http_status == status
    assert ei.value.message


@pytest.mark.parametrize(
    "exc,cause",
    [
        (TimeoutError(), "qrypt_timeout"),
        (TimeoutError(), "qrypt_timeout"),
        (urllib.error.URLError(TimeoutError()), "qrypt_timeout"),
        (urllib.error.URLError("dns failure"), "qrypt_unreachable"),
        (ConnectionResetError(), "qrypt_unreachable"),
        (http.client.IncompleteRead(b"x"), "qrypt_bad_response"),
        (http.client.BadStatusLine("junk"), "qrypt_bad_response"),
    ],
)
def test_network_failures_raise(monkeypatch, exc, cause):
    c, _ = client(monkeypatch, exc)
    with pytest.raises(QryptError) as ei:
        c.fetch_block()
    assert ei.value.cause == cause


def test_token_with_newline_is_a_named_cause_not_a_crash():
    """The real opener rejects a header value holding a newline before any network I/O."""
    c = QryptClient(lambda: "tok\n", url=URL, timeout_s=0.01)
    with pytest.raises(QryptError) as ei:
        c.fetch_block()
    assert ei.value.cause == "qrypt_key_invalid"


def test_redirects_are_refused_so_the_token_never_moves():
    req = urllib.request.Request(URL, headers={"Authorization": "Bearer tok"})
    with pytest.raises(QryptError) as ei:
        qrypt._NoRedirect().redirect_request(req, None, 302, "Found", {}, "http://elsewhere.example/")
    assert ei.value.cause == "qrypt_redirected" and ei.value.http_status == 302
    # and the opener fetch_block uses is the one with this handler, not urllib's default
    handlers = qrypt._OPENER.handlers
    assert any(isinstance(h, qrypt._NoRedirect) for h in handlers)
    assert not any(type(h) is urllib.request.HTTPRedirectHandler for h in handlers)


def test_missing_token_fails_closed(monkeypatch):
    c, _ = client(monkeypatch, FakeResp(ok_body()), token="")
    with pytest.raises(QryptError, match="could not be loaded"):
        c.fetch_block()

    def boom():
        raise RuntimeError("ssm down")

    c2 = QryptClient(boom)
    with pytest.raises(QryptError) as ei:
        c2.fetch_block()
    assert ei.value.cause == "qrypt_key_unavailable"


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        json.dumps({"random": []}).encode(),
        json.dumps({"nope": 1}).encode(),
        json.dumps({"random": ["!!!notbase64"]}).encode(),
        json.dumps({"random": [base64.b64encode(b"short").decode()]}).encode(),
        json.dumps({"random": "abc"}).encode(),
    ],
)
def test_bad_payloads_rejected(raw):
    with pytest.raises(QryptError) as ei:
        parse_response(raw)
    assert ei.value.cause == "qrypt_bad_response"


def test_request_id_from_body():
    raw = json.dumps({"random": [base64.b64encode(BLOCK).decode()], "requestId": "r-1"}).encode()
    assert parse_response(raw).request_id == "r-1"


def test_uses_first_block_when_more_returned():
    other = base64.b64encode(b"\xff" * 1024).decode()
    assert parse_response(ok_body([base64.b64encode(BLOCK).decode(), other])).data == BLOCK


@pytest.mark.parametrize(
    "url",
    [
        "http://api-eus.qrypt.com/api/v1/quantum-entropy",  # not https
        "https://qrypt.com.evil.example/api",  # suffix trick
        "https://evilqrypt.com/api",  # no dot boundary
        "https://user:pw@api-eus.qrypt.com/api",  # credentials in the URL
        "https://10.255.255.1/api/v1/quantum-entropy",
        "https://localhost:8765/api",
        "not a url",
    ],
)
def test_non_qrypt_endpoint_fails_closed_before_token_is_read(monkeypatch, url):
    def no_network(*a, **k):
        raise AssertionError("must not connect")

    monkeypatch.setattr(qrypt, "_open", no_network)
    token_reads = []
    c = QryptClient(lambda: token_reads.append(1) or "tok", url=url)
    with pytest.raises(QryptError) as ei:
        c.fetch_block()
    assert ei.value.cause == "qrypt_endpoint_not_allowed"
    assert token_reads == []


@pytest.mark.parametrize("url", [URL, "https://qrypt.com/x", "https://API-EUS.Qrypt.com:8443/x"])
def test_qrypt_hosts_are_allowed(url):
    assert qrypt.endpoint_host(url).endswith("qrypt.com")
