"""Package import and entry-point smoke tests."""

from __future__ import annotations

import subprocess
import sys

import governance
from governance import __version__


def test_package_import() -> None:
    assert __version__ == "0.1.0"
    assert governance.__name__ == "governance"


def test_entry_point_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "governance"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "governance 0.1.0" in result.stdout
    assert "password=***" in result.stdout
    assert "super-secret" not in result.stdout
