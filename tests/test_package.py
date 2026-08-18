"""Package import and entry-point smoke tests."""

from __future__ import annotations

import subprocess
import sys
from importlib import metadata
from pathlib import Path

import governance
from governance import __version__


def test_package_import() -> None:
    assert __version__ == "1.3.0"
    assert governance.__version__ == "1.3.0"
    assert governance.__name__ == "governance"


def test_pyproject_version_matches_runtime() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'version = "1.3.0"' in text
    assert metadata.version("collibra-governance-automation") == "1.3.0"


def test_entry_point_module_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "governance", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "scan" in result.stdout
    assert "export" in result.stdout
    assert "diff" in result.stdout
    assert "sync" in result.stdout
    assert "config" in result.stdout
    assert "check" in result.stdout
    assert "plan" in result.stdout
    assert "apply" in result.stdout
    assert "preflight" in result.stdout
    assert "password=" not in result.stdout


def test_entry_point_module_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "governance", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"governance {__version__}"
