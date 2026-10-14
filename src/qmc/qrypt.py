"""Qrypt Entropy (EaaS) REST client.

Qrypt is the only entropy source in this system. Every failure raises
QryptError with a cause code; callers reject the job. There is deliberately
no fallback to any local random source.

The endpoint and size limits follow Qrypt's EaaS documentation; the base URL
is configuration (QRYPT_ENTROPY_URL), not a constant baked into callers.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

DEFAULT_URL = "https://api-eus.qrypt.com/api/v1/quantum-entropy"
BLOCK_BYTES = 1024

# Response headers that might carry a request id. Qrypt does not document one;
# we store whatever is present and null otherwise, never an invented value.
# A request id is third-party text that is stored and shown in the console and
# audit bundle, so only an opaque token shape is kept; anything else becomes null.
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
REQUEST_ID_HEADERS = ("x-request-id", "request-id", "x-correlation-id", "x-amzn-requestid")

STATUS_CAUSES = {
    400: "qrypt_bad_request",
    401: "qrypt_unauthorized",
    403: "qrypt_allotment_exhausted",
    429: "qrypt_rate_limited",
    500: "qrypt_internal_error",
    503: "qrypt_entropy_unavailable",
}

CAUSE_MESSAGES = {
    "qrypt_bad_request": "Qrypt rejected the entropy request as malformed (400).",
    "qrypt_unauthorized": "Qrypt token is invalid or expired (401).",
    "qrypt_allotment_exhausted": "Qrypt entropy allotment for this period is used up (403).",
    "qrypt_rate_limited": "Qrypt rate limit hit (429): more than 30 requests in 10 seconds.",
    "qrypt_internal_error": "Qrypt returned an internal error (500).",
    "qrypt_entropy_unavailable": "Qrypt entropy supply is temporarily insufficient (503).",
    "qrypt_http_error": "Qrypt returned an unexpected HTTP status.",
    "qrypt_timeout": "Qrypt did not respond before the timeout.",
    "qrypt_unreachable": "Qrypt could not be reached (network error).",
    "qrypt_bad_response": "Qrypt returned a response that is not a valid entropy block.",
    "qrypt_key_unavailable": "The Qrypt token could not be loaded from SSM.",
}


class QryptError(Exception):
    def __init__(self, cause: str, http_status: int | None = None):
        super().__init__(CAUSE_MESSAGES.get(cause, cause))
        self.cause = cause
        self.http_status = http_status

    @property
    def message(self) -> str:
        return CAUSE_MESSAGES.get(self.cause, self.cause)


@dataclass(frozen=True)
class EntropyBlock:
    data: bytes  # exactly BLOCK_BYTES
    request_id: str | None
    kib: int = 1


class EntropySource(Protocol):
    def fetch_block(self) -> EntropyBlock: ...


class QryptClient:
    """Fetches one 1 KiB block per call. The token is supplied by a callable so
    the handler can load it lazily from SSM and cache it per container."""

    def __init__(self, token_provider, url: str = DEFAULT_URL, timeout_s: float = 5.0):
        self._token_provider = token_provider
        self._url = url
        self._timeout_s = timeout_s

    def fetch_block(self) -> EntropyBlock:
        try:
            token = self._token_provider()
        except Exception as exc:
            raise QryptError("qrypt_key_unavailable") from exc
        if not token:
            raise QryptError("qrypt_key_unavailable")

        req = urllib.request.Request(  # noqa: S310 - fixed https URL from config
            f"{self._url}?size=1",
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310
                raw = resp.read(256 * 1024)
                headers = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as exc:
            raise QryptError(STATUS_CAUSES.get(exc.code, "qrypt_http_error"), exc.code) from None
        except TimeoutError:
            raise QryptError("qrypt_timeout") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise QryptError("qrypt_timeout") from None
            raise QryptError("qrypt_unreachable") from None
        except OSError:
            raise QryptError("qrypt_unreachable") from None

        return parse_response(raw, headers)


def parse_response(raw: bytes, headers: dict[str, str] | None = None) -> EntropyBlock:
    """Parse {"random": ["<base64 of 1024 bytes>"], "size": 1}."""
    try:
        doc = json.loads(raw)
        blocks = doc["random"]
        if not isinstance(blocks, list) or len(blocks) < 1:
            raise ValueError("no blocks")
        data = base64.b64decode(blocks[0], validate=True)
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise QryptError("qrypt_bad_response") from None
    if len(data) != BLOCK_BYTES:
        raise QryptError("qrypt_bad_response")

    candidates = [headers.get(name) for name in REQUEST_ID_HEADERS] if headers else []
    if isinstance(doc, dict):
        candidates += [doc.get(key) for key in ("requestId", "request_id", "id")]
    request_id = next((str(c) for c in candidates if c), None)
    if request_id is not None and not REQUEST_ID_RE.fullmatch(request_id):
        request_id = None
    return EntropyBlock(data=data, request_id=request_id, kib=1)
