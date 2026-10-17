#!/usr/bin/env python3
"""Tiny client for the deployed API (stdlib only).

Reads QMC_API_URL and QMC_API_KEY from the environment or from .env.

  python scripts/qmc_cli.py submit [--paths 1000000 --shards 10 ...] [--wait]
  python scripts/qmc_cli.py get JOB_ID
  python scripts/qmc_cli.py list [--status COMPLETE]
  python scripts/qmc_cli.py replay JOB_ID [--wait]
  python scripts/qmc_cli.py bundle JOB_ID > bundle.json
  python scripts/qmc_cli.py health | system
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

FINAL = {"COMPLETE", "PARTIAL"}


def load_env() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def call(method: str, path: str, body=None, timeout: float = 30) -> tuple[int, dict, dict, float]:
    if not os.environ.get("QMC_API_URL") or not os.environ.get("QMC_API_KEY"):
        raise SystemExit("set QMC_API_URL and QMC_API_KEY (environment or .env) first")
    base = os.environ["QMC_API_URL"].rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 - URL from operator config
        base + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {os.environ['QMC_API_KEY']}", "Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            raw, status, headers = r.read(), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw, status, headers = e.read(), e.code, dict(e.headers)
    except urllib.error.URLError as e:
        raise SystemExit(f"could not reach {base}: {e.reason}") from None
    elapsed = time.perf_counter() - t0
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        doc = {"raw": raw.decode(errors="replace")[:500]}
    return status, doc, headers, elapsed


def wait(job_id: str, timeout_s: float = 300, interval: float = 1.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while True:
        _, job, _, _ = call("GET", f"/jobs/{job_id}")
        if job.get("status") in FINAL or time.monotonic() > deadline:
            return job
        time.sleep(interval)


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    for k, v in (("S0", 100.0), ("K", 100.0), ("r", 0.05), ("sigma", 0.2), ("T", 1.0)):
        s.add_argument(f"--{k}", type=float, default=v)
    s.add_argument("--paths", type=int, default=1_000_000)
    s.add_argument("--shards", type=int, default=10)
    s.add_argument("--fail-shards", type=int, nargs="*")
    s.add_argument("--wait", action="store_true")
    for name in ("get", "replay", "bundle"):
        p = sub.add_parser(name)
        p.add_argument("job_id")
        if name == "replay":
            p.add_argument("--wait", action="store_true")
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    sub.add_parser("health")
    sub.add_parser("system")
    a = ap.parse_args()

    if a.cmd == "submit":
        body = {k: getattr(a, k) for k in ("S0", "K", "r", "sigma", "T", "paths", "shards")}
        if a.fail_shards:
            body["fail_shards"] = a.fail_shards
        status, doc, _, el = call("POST", "/jobs", body)
        print(json.dumps({"http_status": status, "latency_ms": round(el * 1000, 1), **doc}, indent=2))
        if a.wait and status == 202:
            print(json.dumps(wait(doc["job_id"]), indent=2))
    elif a.cmd == "replay":
        status, doc, _, _ = call("POST", f"/jobs/{a.job_id}/replay")
        print(json.dumps({"http_status": status, **doc}, indent=2))
        if a.wait and status == 202:
            print(json.dumps(wait(doc["job_id"]), indent=2))
    elif a.cmd in ("get", "bundle"):
        suffix = "/bundle" if a.cmd == "bundle" else ""
        print(json.dumps(call("GET", f"/jobs/{a.job_id}{suffix}")[1], indent=2))
    elif a.cmd == "list":
        q = f"?status={a.status}" if a.status else ""
        print(json.dumps(call("GET", f"/jobs{q}")[1], indent=2))
    else:
        print(json.dumps(call("GET", f"/{a.cmd}")[1], indent=2))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
