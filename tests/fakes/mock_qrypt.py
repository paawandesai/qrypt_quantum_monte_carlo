"""Mock Qrypt entropy source, for tests only.

This module lives under tests/ and is never packaged into a Lambda zip
(tests/test_packaging.py asserts that). The deployed handler has no switch,
flag or environment variable that can select it; tests inject it directly
into qmc.handlers.api.App(entropy=...).
"""

from __future__ import annotations

import hashlib

from qmc.qrypt import BLOCK_BYTES, EntropyBlock, QryptError


class MockQrypt:
    """Deterministic blocks derived from a counter; or raises a chosen QryptError."""

    def __init__(self, fail_with: str | None = None, http_status: int | None = None, request_id: str | None = None):
        self.calls = 0
        self.fail_with = fail_with
        self.http_status = http_status
        self.request_id = request_id

    def fetch_block(self) -> EntropyBlock:
        self.calls += 1
        if self.fail_with:
            raise QryptError(self.fail_with, self.http_status)
        out = b""
        i = 0
        while len(out) < BLOCK_BYTES:
            out += hashlib.sha256(f"mock-qrypt/{self.calls}/{i}".encode()).digest()
            i += 1
        return EntropyBlock(data=out[:BLOCK_BYTES], request_id=self.request_id, kib=1)
