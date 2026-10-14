"""Qrypt client: every failure maps to a named cause and raises; nothing falls back."""

import base64
import io
import json
import urllib.error

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


def client(monkeypatch, outcome, token="tok"):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.header_items())
        seen["timeout"] = timeout
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(qrypt.urllib.request, "urlopen", fake_urlopen)
    return QryptClient(lambda: token, url="https://qrypt.invalid/api/v1/quantum-entropy", timeout_s=2), seen


def test_success_requests_one_kib_with_bearer(monkeypatch):
    c, seen = client(monkeypatch, FakeResp(ok_body(), {"X-Request-Id": "abc-123"}))
    blk = c.fetch_block()
    assert blk.data == BLOCK and blk.kib == 1 and blk.request_id == "abc-123"
    assert seen["url"].endswith("?size=1")
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["headers"]["Accept"] == "application/json"
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
    err = urllib.error.HTTPError("https://qrypt.invalid", status, "x", {}, None)
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
    ],
)
def test_network_failures_raise(monkeypatch, exc, cause):
    c, _ = client(monkeypatch, exc)
    with pytest.raises(QryptError) as ei:
        c.fetch_block()
    assert ei.value.cause == cause


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
