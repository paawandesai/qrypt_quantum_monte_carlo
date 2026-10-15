#!/usr/bin/env python3
"""Run a command and print its wall time and peak memory (largest child RSS).

Usage: python scripts/peak_mem.py -- <command> [args...]
CI wraps its heaviest steps with this so each job log records the numbers the
per-job memory budget is checked against. Exit status is the command's.
"""

from __future__ import annotations

import resource
import subprocess
import sys
import time


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        raise SystemExit("usage: peak_mem.py -- <command> [args...]")
    start = time.monotonic()
    code = subprocess.call(argv)  # noqa: S603 - the caller's own command
    secs = time.monotonic() - start
    # ru_maxrss is KiB on Linux: the largest single descendant process, not a sum.
    peak_mib = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    print(f"peak_mem: {secs:.1f} s, peak RSS {peak_mib:.0f} MiB: {' '.join(argv)[:120]}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
