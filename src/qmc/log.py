"""Structured logging with an allow-list of fields.

Function URL events carry the Authorization header, so nothing here ever logs
an event, a body or a header. Only the fields below are emitted; anything else
passed in is dropped silently.
"""

from __future__ import annotations

import json
import sys

ALLOWED = frozenset(
    {
        "event",
        "request_id",
        "method",
        "path",
        "status",
        "job_id",
        "replay_of",
        "shard_index",
        "shards",
        "paths",
        "attempt",
        "code",
        "cause",
        "http_status",
        "duration_ms",
        "job_status",
        "count",
    }
)


def log(level: str, **fields) -> None:
    rec = {"level": level}
    rec.update({k: v for k, v in fields.items() if k in ALLOWED})
    sys.stdout.write(json.dumps(rec, default=str) + "\n")


def info(**fields) -> None:
    log("INFO", **fields)


def warn(**fields) -> None:
    log("WARN", **fields)


def error(**fields) -> None:
    log("ERROR", **fields)
