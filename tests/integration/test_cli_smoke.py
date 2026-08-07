"""CLI integration smoke against the local PostgreSQL demo (no Collibra network)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.cli_integration


def _run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_cli_console_script_and_module_smoke(tmp_path: Path) -> None:
    governance_bin = shutil.which("governance")
    assert governance_bin is not None, "console script 'governance' must be installed"

    help_module = _run([sys.executable, "-m", "governance", "--help"])
    assert help_module.returncode == 0
    assert "scan" in help_module.stdout
    assert "secret" not in help_module.stdout.lower()
    assert "password" not in help_module.stderr.lower()

    scan = _run([governance_bin, "scan"])
    assert scan.returncode == 0, scan.stderr
    assert "source=governance-demo" in scan.stdout
    assert "database=governance_demo" in scan.stdout
    assert "tables=" in scan.stdout

    inventory_path = tmp_path / "inventory.json"
    export = _run([governance_bin, "export", "--output", str(inventory_path)])
    assert export.returncode == 0, export.stderr
    assert inventory_path.is_file()
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert payload["inventory_schema"] == "governance-metadata-inventory"
    assert payload["source"]["name"] == "governance-demo"

    diff = _run([governance_bin, "diff", "--mode", "mock"])
    assert diff.returncode == 0, diff.stderr
    assert "mode=mock" in diff.stdout
    assert "writes=0" in diff.stdout

    dry_run = _run([governance_bin, "sync", "--mode", "mock"])
    assert dry_run.returncode == 0, dry_run.stderr
    assert "mode=mock" in dry_run.stdout
    assert "dry_run=true" in dry_run.stdout
    assert "applied=0" in dry_run.stdout
    assert "success=true" in dry_run.stdout

    apply = _run([governance_bin, "sync", "--mode", "mock", "--apply"])
    assert apply.returncode == 0, apply.stderr
    assert "mode=mock" in apply.stdout
    assert "dry_run=false" in apply.stdout
    assert "success=true" in apply.stdout
    applied_line = next(line for line in apply.stdout.splitlines() if line.startswith("applied="))
    assert int(applied_line.split("=", 1)[1]) > 0

    combined = "".join(
        [
            scan.stdout,
            scan.stderr,
            export.stdout,
            export.stderr,
            diff.stdout,
            diff.stderr,
            dry_run.stdout,
            dry_run.stderr,
            apply.stdout,
            apply.stderr,
        ]
    )
    assert "Authorization" not in combined
    assert "bearer" not in combined.lower()
