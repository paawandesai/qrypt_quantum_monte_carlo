#!/usr/bin/env python3
"""Build the three Lambda zips into build/: api.zip, worker.zip, reconciler.zip.

- Only src/qmc is packaged. tests/ (and the mock Qrypt client in it) never is.
- The worker zip adds numpy from requirements-worker.txt as a manylinux aarch64
  wheel for Python 3.13 (the Lambda runtime and architecture we deploy).
- Zips are byte-reproducible (sorted entries, fixed timestamps, no .pyc), so
  `terraform plan` after a rebuild shows no drift.
- build_info.json records the numpy version and architecture each package was
  built for; replay compares it with the job's recorded fingerprint.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
BUILD = ROOT / "build"
PY_VERSION = "3.13"
PLATFORM = "manylinux_2_28_aarch64"
ARCH = "arm64"
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def numpy_pin() -> str:
    for line in (ROOT / "requirements-worker.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line.startswith("numpy=="):
            return line.split("==")[1]
    raise SystemExit("numpy pin not found in requirements-worker.txt")


def add_tree(zf: zipfile.ZipFile, base: Path, arc_prefix: str = "") -> None:
    files = sorted(
        p
        for p in base.rglob("*")
        if p.is_file() and not (set(p.relative_to(base).parts) & SKIP_DIRS) and p.suffix != ".pyc"
    )
    for p in files:
        add_bytes(zf, arc_prefix + p.relative_to(base).as_posix(), p.read_bytes(), p.stat().st_mode)


def add_bytes(zf: zipfile.ZipFile, name: str, data: bytes, mode: int = 0o100644) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = ((mode & 0o111) | 0o100644) << 16
    zf.writestr(info, data, compresslevel=9)


def build_zip(name: str, info: dict, deps_dir: Path | None) -> Path:
    out = BUILD / f"{name}.zip"
    with zipfile.ZipFile(out, "w") as zf:
        add_tree(zf, SRC / "qmc", "qmc/")
        add_bytes(zf, "build_info.json", (json.dumps(info, sort_keys=True, indent=2) + "\n").encode())
        if deps_dir:
            add_tree(zf, deps_dir)
    return out


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    pin = numpy_pin()
    deps = BUILD / "worker-deps"
    marker = deps / f".numpy-{pin}-{PLATFORM}"
    if not marker.exists():
        shutil.rmtree(deps, ignore_errors=True)
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--no-compile",
                "--no-deps",
                "--only-binary=:all:",
                "--platform",
                PLATFORM,
                "--python-version",
                PY_VERSION,
                "--implementation",
                "cp",
                "--target",
                str(deps),
                f"numpy=={pin}",
            ],
            check=True,
        )
        for p in deps.glob("*.dist-info"):
            (p / "RECORD").unlink(missing_ok=True)  # contains paths only; drop for reproducibility
        marker.touch()

    with_numpy = {"numpy_version": pin, "arch": ARCH, "python": PY_VERSION, "source": "build"}
    for name, d in (("api", None), ("reconciler", None), ("worker", deps)):
        info = dict(with_numpy, package=name, numpy_bundled=d is not None)
        out = build_zip(name, info, d)
        print(f"{out.relative_to(ROOT)}  {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
