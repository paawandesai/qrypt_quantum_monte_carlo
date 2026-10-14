#!/usr/bin/env python3
"""Deployed fail-closed test: with Qrypt unreachable, a submit is rejected and
no job is created. Run against a deployed stack, with the operator's approval:
it briefly changes the API function's configuration.

  python scripts/deployed_failclosed_test.py --function qmc-dev-api

Steps: save the function's environment; point QRYPT_ENTROPY_URL at a
non-routable address; submit; assert HTTP 503/504 with error
"entropy_unavailable", job_created false, and no new job in GET /jobs;
restore the saved environment exactly (so `terraform plan` shows no drift).
There is no runtime flag that switches the deployed code to a mock: this test
exercises the real client against a real network failure.
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3
from qmc_cli import call, load_env

UNREACHABLE = "https://10.255.255.1/api/v1/quantum-entropy"  # non-routable; connect times out


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--function", required=True)
    a = ap.parse_args()
    lam = boto3.client("lambda")
    saved = lam.get_function_configuration(FunctionName=a.function)["Environment"]["Variables"]
    before = {j["job_id"] for j in call("GET", "/jobs?limit=100")[1].get("jobs", [])}
    try:
        lam.update_function_configuration(
            FunctionName=a.function, Environment={"Variables": {**saved, "QRYPT_ENTROPY_URL": UNREACHABLE}}
        )
        lam.get_waiter("function_updated_v2").wait(FunctionName=a.function)
        status, doc, _, el = call(
            "POST", "/jobs", {"S0": 100, "K": 100, "r": 0.05, "sigma": 0.2, "T": 1, "paths": 1000, "shards": 1}
        )
    finally:
        lam.update_function_configuration(FunctionName=a.function, Environment={"Variables": saved})
        lam.get_waiter("function_updated_v2").wait(FunctionName=a.function)
    after = {j["job_id"] for j in call("GET", "/jobs?limit=100")[1].get("jobs", [])}
    report = {
        "http_status": status,
        "latency_ms": round(el * 1000),
        "body": doc,
        "new_jobs": sorted(after - before),
        "config_restored": True,
    }
    print(json.dumps(report, indent=2))
    ok = (
        status in (503, 504)
        and doc.get("error") == "entropy_unavailable"
        and doc.get("job_created") is False
        and not (after - before)
    )
    print("PASS: rejected, no job created" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
