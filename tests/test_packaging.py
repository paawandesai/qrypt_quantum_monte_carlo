"""The mock Qrypt client and tests are never packaged; packages are reproducible."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build", ROOT / "scripts" / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

FORBIDDEN = ("mock", "fakes", "tests/", "conftest", ".env")


def names_in(path):
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def check(names):
    assert "qmc/handlers/api.py" in names and "build_info.json" in names
    bad = [n for n in names if any(f in n.lower() for f in FORBIDDEN) and not n.startswith("numpy")]
    assert bad == []


def test_api_zip_has_no_mock_and_is_reproducible(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "BUILD", tmp_path)
    a = build.build_zip("api", {"numpy_version": "x", "arch": "arm64"}, None)
    first = a.read_bytes()
    a2 = build.build_zip("api", {"numpy_version": "x", "arch": "arm64"}, None)
    assert a2.read_bytes() == first
    check(names_in(a))


@pytest.mark.parametrize("name", ["api", "worker", "reconciler"])
def test_built_zips_have_no_mock(name):
    path = ROOT / "build" / f"{name}.zip"
    if not path.exists():
        pytest.skip("run scripts/build.py first")
    names = names_in(path)
    check(names)
    if name == "worker":
        assert any(n.startswith("numpy/") for n in names)
    else:
        assert not any(n.startswith("numpy/") for n in names)
