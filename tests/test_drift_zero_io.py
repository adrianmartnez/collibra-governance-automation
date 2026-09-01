"""Zero remote I/O proofs for governance drift."""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from conftest_drift import write_identical_comparison, write_policy
from governance.cli import main


def test_drift_in_process_no_remote_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    comparison = tmp_path / "cmp.json"
    write_identical_comparison(comparison)
    assert main(["drift", "--comparison", str(comparison)]) == 0
    assert calls == []


def test_drift_in_process_different_comparison_no_remote_calls(
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
    monkeypatch.setattr("socket.create_connection", boom("socket.create_connection"))
    monkeypatch.setattr(urllib.request, "urlopen", boom("urllib.request.urlopen"))

    from conftest_drift import write_different_description_comparison, write_policy

    comparison = tmp_path / "cmp.json"
    policy = tmp_path / "policy.yaml"
    write_different_description_comparison(comparison)
    write_policy(
        policy,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
""",
    )
    assert (
        main(
            ["drift", "--comparison", str(comparison), "--policy", str(policy), "--format", "json"]
        )
        == 0
    )
    assert calls == []


def test_drift_subprocess_offline_smoke(tmp_path: Path) -> None:
    comparison = tmp_path / "cmp.json"
    write_identical_comparison(comparison)
    env = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "COLLIBRA_MODE"}}
    env.pop("COLLIBRA_BASE_URL", None)
    env.pop("COLLIBRA_CLIENT_ID", None)
    env.pop("COLLIBRA_CLIENT_SECRET", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "governance",
            "drift",
            "--comparison",
            str(comparison),
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
    assert '"status": "no_difference"' in result.stdout
    assert '"writes_performed": 0' in result.stdout


def test_drift_with_policy_subprocess_offline_smoke(tmp_path: Path) -> None:
    comparison = tmp_path / "cmp.json"
    policy = tmp_path / "policy.yaml"
    write_identical_comparison(comparison)
    write_policy(
        policy,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
""",
    )
    env = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "COLLIBRA_MODE"}}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "governance",
            "drift",
            "--comparison",
            str(comparison),
            "--policy",
            str(policy),
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
    assert '"status": "no_difference"' in result.stdout


def test_drift_module_import_boundary() -> None:
    import governance.drift as drift
    import governance.drift.classify as classify_mod
    import governance.drift.result as result_mod

    for module in (drift, classify_mod, result_mod):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "governance.reconciliation" not in source
        assert "governance.scanner" not in source
        assert "governance.integrations" not in source
