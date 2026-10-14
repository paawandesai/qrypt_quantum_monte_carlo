"""Job ids and timestamps.

Job ids are derived, not drawn: a new job's id hashes its Qrypt block hash and
creation time; a replay's id hashes the original id, the word "replay", the
time and an attempt counter. The deployed code therefore needs no local random
source at all. Uniqueness is enforced by a conditional put, which retries with
the next attempt number on the (practically impossible) collision.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def epoch_s(dt: datetime) -> int:
    return int(dt.timestamp())


def plus(dt: datetime, seconds: float) -> datetime:
    return dt + timedelta(seconds=seconds)


def derive_job_id(*parts: str) -> str:
    h = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return h[:32]


def is_job_id(value: str) -> bool:
    return isinstance(value, str) and bool(JOB_ID_RE.match(value))
