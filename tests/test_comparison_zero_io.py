"""Zero remote I/O proofs for governance compare."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest_comparison import build_snapshot
from governance.cli import main
from governance.snapshots import write_snapshot


def test_compare_in_process_no_remote_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def boom(name: str):
        def _inner(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be called")

        return _inner

    monkeypatch.setattr(
        "governance.scanner.postgres.PostgresMetadataScanner",
        boom("PostgresMetadataScanner"),
    )
    monkeypatch.setattr(
        "governance.integrations.collibra.build_collibra_adapter",
        boom("build_collibra_adapter"),
    )
    monkeypatch.setattr(
        "governance.reconciliation.compose_reconciliation_sources",
        boom("compose_reconciliation_sources"),
    )

    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    write_snapshot(build_snapshot(), a)
    write_snapshot(build_snapshot(), b)
    assert main(["compare", "--baseline", str(a), "--candidate", str(b)]) == 0
    assert calls == []


def test_compare_subprocess_offline_smoke(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    write_snapshot(build_snapshot(), a)
    write_snapshot(build_snapshot(), b)
    env = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "COLLIBRA_MODE"}}
    env.pop("COLLIBRA_BASE_URL", None)
    env.pop("COLLIBRA_CLIENT_ID", None)
    env.pop("COLLIBRA_CLIENT_SECRET", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "governance",
            "compare",
            "--baseline",
            str(a),
            "--candidate",
            str(b),
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0
    assert '"status": "identical"' in result.stdout
    assert '"writes_performed": 0' in result.stdout


def test_comparison_module_import_boundary() -> None:
    import governance.comparison as comparison
    import governance.comparison.compare as compare_mod
    import governance.comparison.projection as projection_mod

    for module in (comparison, compare_mod, projection_mod):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "governance.reconciliation" not in source
        assert "governance.scanner" not in source
        assert "governance.integrations" not in source
