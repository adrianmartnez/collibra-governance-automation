"""Clean-environment packaging smoke for Provider SDK discovery (#92).

Requires local wheel builds and a wheelhouse of runtime dependencies.
Marked ``provider_packaging`` so normal unit runs do not hit packaging infrastructure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

pytestmark = pytest.mark.provider_packaging

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "providers" / "example_provider"


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"command failed: {command}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def _ensure_build_available() -> None:
    try:
        import build  # noqa: F401
    except ImportError:
        pytest.fail(
            "provider packaging smoke requires the 'build' package "
            "(pip install build). Packaging infrastructure unavailable.",
            pytrace=False,
        )


def test_clean_venv_discovers_fixture_provider_from_wheels(tmp_path: Path) -> None:
    _ensure_build_available()

    core_dist = tmp_path / "core-dist"
    fixture_dist = tmp_path / "fixture-dist"
    wheelhouse = tmp_path / "wheelhouse"
    venv_dir = tmp_path / "venv"
    core_dist.mkdir()
    fixture_dist.mkdir()
    wheelhouse.mkdir()

    _run(
        [sys.executable, "-m", "build", "--outdir", str(core_dist)],
        cwd=REPO_ROOT,
    )
    _run(
        [sys.executable, "-m", "build", "--outdir", str(fixture_dist)],
        cwd=FIXTURE_ROOT,
    )

    core_wheels = sorted(core_dist.glob("*.whl"))
    fixture_wheels = sorted(fixture_dist.glob("*.whl"))
    assert core_wheels, "core wheel was not built"
    assert fixture_wheels, "fixture wheel was not built"

    # Prefetch runtime dependencies into a local wheelhouse so the clean venv
    # install can use --no-index and does not silently rely on live PyPI.
    download = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--dest",
            str(wheelhouse),
            str(core_wheels[0]),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if download.returncode != 0:
        pytest.fail(
            "unable to provision local wheelhouse for provider packaging smoke; "
            "pip download of core wheel dependencies failed. "
            "Ensure packaging infrastructure (pip + dependency index/cache) is available.\n"
            f"stdout:\n{download.stdout}\nstderr:\n{download.stderr}",
            pytrace=False,
        )

    for wheel in (*core_wheels, *fixture_wheels):
        target = wheelhouse / wheel.name
        target.write_bytes(wheel.read_bytes())

    builder = venv.EnvBuilder(with_pip=True, system_site_packages=False, clear=True)
    builder.create(venv_dir)
    python = _venv_python(venv_dir)
    assert python.is_file(), f"venv python missing: {python}"

    _run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            str(wheelhouse / core_wheels[0].name),
            str(wheelhouse / fixture_wheels[0].name),
        ]
    )

    probe = """
from governance.providers import (
    PROVIDER_ENTRY_POINT_GROUP,
    PROVIDER_SDK_API_VERSION,
    discover_providers,
)
assert PROVIDER_SDK_API_VERSION == "1"
assert PROVIDER_ENTRY_POINT_GROUP == "governance.providers"
registry = discover_providers()
provider = registry.get("example.fixture")
assert provider.descriptor.provider_id == "example.fixture"
assert "lineage" in {cap.value for cap in provider.descriptor.capabilities}
print("provider-discovery-ok")
"""
    completed = subprocess.run(
        [str(python), "-c", probe],
        check=False,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        cwd=str(tmp_path),
    )
    assert completed.returncode == 0, (
        f"clean venv discovery failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    assert "provider-discovery-ok" in completed.stdout
