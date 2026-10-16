"""Qrypt Entropy (EaaS) REST client.

Qrypt is the only entropy source in this system. Every failure raises
QryptError with a cause code; callers reject the job. There is deliberately
no fallback to any local random source.

The endpoint and size limits follow Qrypt's EaaS documentation; the base URL
is configuration (QRYPT_ENTROPY_URL), not a constant baked into callers. The
configured URL must be https on a qrypt.com host, otherwise every fetch fails
closed, and the host that answered is recorded with each block, so a job's
`entropy_source: "qrypt"` is checked rather than assumed.
"""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Protocol

DEFAULT_URL = "https://api-eus.qrypt.com/api/v1/quantum-entropy"
BLOCK_BYTES = 1024
ALLOWED_HOST = "qrypt.com"  # the host itself or any subdomain of it
# Qrypt sits behind Cloudflare, which answers urllib's default User-Agent
# ("Python-urllib/3.x") with 403 "error code: 1010". Identify the client instead.
USER_AGENT = "qrypt-monte-carlo/0.1"

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
    "qrypt_allotment_exhausted": "Qrypt refused the request (403): forbidden, or the entropy quota is used up.",
    "qrypt_rate_limited": "Qrypt rate limit hit (429): more than 30 requests in 10 seconds.",
    "qrypt_internal_error": "Qrypt returned an internal error (500).",
    "qrypt_entropy_unavailable": "Qrypt entropy supply is temporarily insufficient (503).",
    "qrypt_http_error": "Qrypt returned an unexpected HTTP status.",
    "qrypt_timeout": "Qrypt did not respond before the timeout.",
    "qrypt_unreachable": "Qrypt could not be reached (network error).",
    "qrypt_bad_response": "Qrypt returned a response that is not a valid entropy block.",
    "qrypt_key_invalid": "The stored Qrypt token is not a valid HTTP header value (check SSM for stray characters).",
    "qrypt_redirected": "Qrypt answered with a redirect; it is refused so the token is never sent to another host.",
    "qrypt_key_unavailable": "The Qrypt token could not be loaded from SSM.",
    "qrypt_endpoint_not_allowed": "The configured entropy URL is not an https URL on a qrypt.com host.",
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
    endpoint_host: str | None = None  # host that served the block


def endpoint_host(url: str) -> str:
    """Return the host of an allowed Qrypt URL; raise QryptError otherwise."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise QryptError("qrypt_endpoint_not_allowed") from None
    if parts.scheme != "https" or parts.username or parts.password:
        raise QryptError("qrypt_endpoint_not_allowed")
    if host != ALLOWED_HOST and not host.endswith("." + ALLOWED_HOST):
        raise QryptError("qrypt_endpoint_not_allowed")
    return host


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib would otherwise resend the Authorization header to
    whatever host (or plain-http URL) a 3xx names, bypassing the host allow-list."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise QryptError("qrypt_redirected", code)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(req: urllib.request.Request, timeout: float):
    return _OPENER.open(req, timeout=timeout)


class EntropySource(Protocol):
    def fetch_block(self) -> EntropyBlock: ...


class QryptClient:
    """Fetches one 1 KiB block per call. The token is supplied by a callable so
    the handler can load it lazily from SSM and cache it (for 1 hour)."""

    def __init__(self, token_provider, url: str = DEFAULT_URL, timeout_s: float = 5.0):
        self._token_provider = token_provider
        self._url = url
        self._timeout_s = timeout_s

    def fetch_block(self) -> EntropyBlock:
        host = endpoint_host(self._url)  # before the token is loaded or sent anywhere
        try:
            token = self._token_provider()
        except Exception as exc:
            raise QryptError("qrypt_key_unavailable") from exc
        if not token:
            raise QryptError("qrypt_key_unavailable")

        req = urllib.request.Request(  # noqa: S310 - fixed https URL from config
            f"{self._url}?size=1",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": USER_AGENT,
            },
            method="GET",
        )
        try:
            with _open(req, self._timeout_s) as resp:
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
        except http.client.HTTPException:  # IncompleteRead, BadStatusLine, ...
            raise QryptError("qrypt_bad_response") from None
        except ValueError:  # http.client rejects a token holding a newline or control character
            raise QryptError("qrypt_key_invalid") from None

        return replace(parse_response(raw, headers), endpoint_host=host)


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
