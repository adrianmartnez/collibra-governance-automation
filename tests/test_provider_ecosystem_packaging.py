"""Clean-venv ecosystem proof for independent example.catalog (#100)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

pytestmark = pytest.mark.provider_packaging

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = REPO_ROOT / "tests" / "fixtures" / "providers" / "governance_provider_example"
SAMPLE_ROOT = SNAPSHOT_ROOT / "sample"


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"command failed: {command}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def _clean_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}


def _ensure_build_available() -> None:
    try:
        import build  # noqa: F401
    except ImportError:
        pytest.fail(
            "provider packaging smoke requires the 'build' package "
            "(pip install build). Packaging infrastructure unavailable.",
            pytrace=False,
        )


def _build_wheels(tmp_path: Path) -> tuple[Path, Path, Path]:
    _ensure_build_available()
    assert (SNAPSHOT_ROOT / "UPSTREAM.txt").is_file()
    upstream = (SNAPSHOT_ROOT / "UPSTREAM.txt").read_text(encoding="utf-8")
    assert "UPSTREAM_REPOSITORY=adrianmartnez/governance-provider-example" in upstream
    assert "UPSTREAM_COMMIT=764a14a658a66c839523788ee98fab62a6c2d038" in upstream

    core_dist = tmp_path / "core-dist"
    provider_dist = tmp_path / "provider-dist"
    wheelhouse = tmp_path / "wheelhouse"
    core_dist.mkdir()
    provider_dist.mkdir()
    wheelhouse.mkdir()

    _run([sys.executable, "-m", "build", "--outdir", str(core_dist)], cwd=REPO_ROOT)
    _run(
        [sys.executable, "-m", "build", "--outdir", str(provider_dist)],
        cwd=SNAPSHOT_ROOT,
    )

    core_wheels = sorted(core_dist.glob("*.whl"))
    provider_wheels = sorted(provider_dist.glob("*.whl"))
    assert core_wheels, "core wheel was not built"
    assert provider_wheels, "provider wheel was not built"

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
            "unable to provision local wheelhouse for ecosystem packaging smoke\n"
            f"stdout:\n{download.stdout}\nstderr:\n{download.stderr}",
            pytrace=False,
        )

    for wheel in (*core_wheels, *provider_wheels):
        (wheelhouse / wheel.name).write_bytes(wheel.read_bytes())

    return core_wheels[0], provider_wheels[0], wheelhouse


def _create_venv(path: Path) -> Path:
    builder = venv.EnvBuilder(with_pip=True, system_site_packages=False, clear=True)
    builder.create(path)
    python = _venv_python(path)
    assert python.is_file()
    _run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    return python


def test_clean_venv_example_catalog_ecosystem_proof(tmp_path: Path) -> None:
    core_wheel, provider_wheel, wheelhouse = _build_wheels(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    # Copy sample artifacts outside the repo checkout for the CLI run.
    sample = work / "sample"
    shutil.copytree(SAMPLE_ROOT, sample)

    # Environment A: core only — provider absent
    venv_a = tmp_path / "venv-a"
    python_a = _create_venv(venv_a)
    _run(
        [
            str(python_a),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            str(wheelhouse / core_wheel.name),
        ]
    )
    absent = """
from governance.providers import discover_providers
registry = discover_providers()
try:
    registry.get("example.catalog")
except Exception as exc:
    assert type(exc).__name__ in {"ProviderRegistryError", "KeyError", "ProviderError"}
    print("provider-absent-ok")
else:
    raise SystemExit("example.catalog unexpectedly present")
"""
    completed = subprocess.run(
        [str(python_a), "-c", absent],
        check=False,
        capture_output=True,
        text=True,
        env=_clean_env(),
        cwd=str(work),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "provider-absent-ok" in completed.stdout

    # Environment B: core + provider — discovery, conformance, impact
    venv_b = tmp_path / "venv-b"
    python_b = _create_venv(venv_b)
    _run(
        [
            str(python_b),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            str(wheelhouse / core_wheel.name),
            str(wheelhouse / provider_wheel.name),
        ]
    )

    present = """
from pathlib import Path
from governance.conformance import (
    GraphCase,
    ObservationsCase,
    RegistrationCase,
    assert_conformance,
    empty_runtime_context,
    run_provider_conformance,
)
from governance.providers import discover_providers
from governance_provider_example.provider import register

registry = discover_providers()
provider = registry.get("example.catalog")
assert provider.descriptor.provider_id == "example.catalog"
caps = {cap.value for cap in provider.descriptor.capabilities}
assert caps == {"governance_graph", "property_observations"}

registration = register()
context = empty_runtime_context(
    {"path": "catalog.json", "namespace": "demo"},
    config_root=str(Path("sample").resolve()),
)
report = run_provider_conformance(
    register=RegistrationCase(register=register),
    scenarios=(
        GraphCase(registration=registration, context=context),
        ObservationsCase(registration=registration, context=context),
    ),
)
assert_conformance(report)
print("provider-present-conformance-ok")
"""
    completed = subprocess.run(
        [str(python_b), "-c", present],
        check=False,
        capture_output=True,
        text=True,
        env=_clean_env(),
        cwd=str(work),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "provider-present-conformance-ok" in completed.stdout

    impact_out = work / "impact.json"
    completed = subprocess.run(
        [
            str(python_b),
            "-m",
            "governance",
            "impact",
            "--config",
            str(sample / "governance.yaml"),
            "--namespace",
            "demo",
            "--changes",
            str(sample / "changes.json"),
            "--output",
            str(impact_out),
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_clean_env(),
        cwd=str(work),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(impact_out.read_text(encoding="utf-8"))
    assert payload["result_version"] == "1"
    assert payload["writes_performed"] == 0
    assert payload["status"] in {"clear", "impacted"}

    # Removal proof: uninstall provider from B
    _run([str(python_b), "-m", "pip", "uninstall", "-y", "governance-provider-example"])
    removed = """
from governance.providers import discover_providers
registry = discover_providers()
try:
    registry.get("example.catalog")
except Exception:
    print("provider-removed-ok")
else:
    raise SystemExit("example.catalog still present after uninstall")
"""
    completed = subprocess.run(
        [str(python_b), "-c", removed],
        check=False,
        capture_output=True,
        text=True,
        env=_clean_env(),
        cwd=str(work),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "provider-removed-ok" in completed.stdout
