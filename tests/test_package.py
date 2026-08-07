"""Package import and entry-point smoke tests."""

from __future__ import annotations

import subprocess
import sys

import governance
from governance import __version__


def test_package_import() -> None:
    assert __version__ == governance.__version__
    assert governance.__name__ == "governance"


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
