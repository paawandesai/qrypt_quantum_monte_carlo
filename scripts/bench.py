#!/usr/bin/env python3
"""Benchmarks against the deployed stack. Nothing is stubbed: every submission
fetches a real 1 KiB Qrypt block and every job runs real workers.

  python scripts/bench.py latency [--n 50]     # submit latency, p50/p95
  python scripts/bench.py wallclock [--runs 5] # 1M-path submit -> COMPLETE
  python scripts/bench.py gbs [--days 3]       # billed GB-s from Lambda REPORT logs

Writes JSON results to bench/results-<kind>-<UTC timestamp>.json so the
numbers in the README come from committed script output, not hand-typed.

Entropy cost: one KiB per submission (latency: N KiB; wallclock: runs KiB).
Submissions are paced at 1 per second to stay under our own Qrypt rate
limiter (14 per 10 s). The first request after a deploy or idle period is a
cold start; it is reported separately and excluded from the warm percentiles.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from qmc_cli import FINAL, call, load_env, wait

OUT = Path(__file__).resolve().parent.parent / "bench"
SMALL = {"S0": 100, "K": 100, "r": 0.05, "sigma": 0.2, "T": 1, "paths": 10_000, "shards": 1}
STD = {"S0": 100, "K": 100, "r": 0.05, "sigma": 0.2, "T": 1, "paths": 1_000_000, "shards": 10}


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return xs[k]


def latency(n: int) -> dict:
    samples = []
    for i in range(n):
        status, doc, _, el = call("POST", "/jobs", SMALL)
        samples.append({"i": i, "status": status, "ms": round(el * 1000, 1), "error": doc.get("error")})
        time.sleep(1.0)
    ok = [s["ms"] for s in samples if s["status"] == 202]
    warm = ok[1:]
    return {
        "n": n,
        "accepted": len(ok),
        "first_request_ms": ok[0] if ok else None,
        "warm_p50_ms": statistics.median(warm) if warm else None,
        "warm_p95_ms": pct(warm, 95) if warm else None,
        "warm_max_ms": max(warm) if warm else None,
        "samples": samples,
    }


def wallclock(runs: int) -> dict:
    out = []
    for _ in range(runs):
        t0 = time.perf_counter()
        status, doc, _, _ = call("POST", "/jobs", STD)
        if status != 202:
            out.append({"status": status, "error": doc})
            continue
        job = wait(doc["job_id"], timeout_s=300, interval=0.5)
        secs = time.perf_counter() - t0
        res = job.get("result") or {}
        out.append(
            {
                "job_id": doc["job_id"],
                "final_status": job.get("status"),
                "seconds_submit_to_final": round(secs, 2),
                "estimate": res.get("estimate"),
                "std_error": res.get("std_error"),
                "z_vs_black_scholes": res.get("z_vs_black_scholes"),
                "within_3_se": abs(float(res["z_vs_black_scholes"])) < 3 if res.get("z_vs_black_scholes") else None,
            }
        )
        time.sleep(2)
    done = [r["seconds_submit_to_final"] for r in out if r.get("final_status") in FINAL]
    return {
        "runs": runs,
        "max_s": max(done) if done else None,
        "median_s": statistics.median(done) if done else None,
        "results": out,
    }


# Monthly invocations for the README workload (100 jobs/day, 1M paths, 10 shards).
WORKLOAD = {"api": 93_000, "worker": 30_000, "reconciler": 8_640}
REPORT_RE = re.compile(
    r"Billed Duration: ([\d.]+) ms\s+Memory Size: (\d+) MB\s+"
    r"Max Memory Used: (\d+) MB(?:\s+Init Duration: ([\d.]+) ms)?"
)


def gbs(days: int) -> dict:
    """Billed duration (INIT included on cold starts) from each function's REPORT
    lines, projected onto the README workload. Read-only: CloudWatch Logs only."""
    start_ms = int((time.time() - days * 86400) * 1000)
    funcs = {}
    total = 0.0
    for fn, monthly in WORKLOAD.items():
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [  # noqa: S607 - the operator's AWS CLI on PATH
                "aws",
                "logs",
                "filter-log-events",
                "--log-group-name",
                f"/aws/lambda/qmc-dev-{fn}",
                "--filter-pattern",
                '"REPORT RequestId"',
                "--start-time",
                str(start_ms),
                "--query",
                "events[].message",
                "--output",
                "json",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        rows = [m.groups() for m in map(REPORT_RE.search, json.loads(out) or []) if m]
        billed = [float(r[0]) for r in rows]
        mem_mb = int(rows[0][1]) if rows else 0
        mean_ms = statistics.fmean(billed) if billed else 0.0
        monthly_gbs = mean_ms / 1000 * mem_mb / 1024 * monthly
        total += monthly_gbs
        funcs[fn] = {
            "invocations": len(rows),
            "cold_starts": sum(1 for r in rows if r[3]),
            "memory_mb": mem_mb,
            "max_memory_used_mb": max((int(r[2]) for r in rows), default=0),
            "billed_mean_ms": round(mean_ms, 1),
            "billed_p50_ms": pct(billed, 50) if billed else None,
            "billed_p95_ms": pct(billed, 95) if billed else None,
            "monthly_invocations": monthly,
            "monthly_gb_s": round(monthly_gbs),
        }
    return {"days": days, "functions": funcs, "monthly_gb_s_total": round(total), "free_gb_s": 400_000}


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["latency", "wallclock", "gbs"])
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--days", type=int, default=3)
    a = ap.parse_args()
    started = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if a.kind == "gbs":
        result = gbs(a.days)
    else:
        result = latency(a.n) if a.kind == "latency" else wallclock(a.runs)
    result["measured_at"] = started
    OUT.mkdir(exist_ok=True)
    path = OUT / f"results-{a.kind}-{started}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    summary = {k: v for k, v in result.items() if k not in ("samples", "results", "functions")}
    print(json.dumps(summary, indent=2))
    print(f"wrote {path.relative_to(OUT.parent)}")


if __name__ == "__main__":
    main()
