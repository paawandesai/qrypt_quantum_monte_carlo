"""CloudWatch custom metrics via the Embedded Metric Format (EMF).

A metric is a structured log line that CloudWatch Logs turns into a metric, so
no PutMetricData call (and no IAM wildcard for it) is needed. Dimensions are
Environment and Function only: a JobId dimension would create a billed metric
per job. The job id belongs in log fields, never in a dimension.
"""

from __future__ import annotations

import json
import os
import sys

from . import ids

NAMESPACE = "QryptMC"
UNITS = {
    "JobsSubmitted": "Count",
    "PathsSimulated": "Count",
    "EntropyKiBConsumed": "Kilobytes",
    "ShardFailures": "Count",
    "WorkerErrors": "Count",
    "QryptFailures": "Count",
}


def emit(**values: float) -> None:
    unknown = set(values) - set(UNITS)
    if unknown:
        raise ValueError(f"unknown metric(s): {sorted(unknown)}")
    doc = {
        "_aws": {
            "Timestamp": int(ids.now().timestamp() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [["Environment", "Function"]],
                    "Metrics": [{"Name": k, "Unit": UNITS[k]} for k in values],
                }
            ],
        },
        "Environment": os.environ.get("ENVIRONMENT", "local"),
        "Function": os.environ.get("FUNCTION_ROLE", "local"),
        **values,
    }
    sys.stdout.write(json.dumps(doc) + "\n")
