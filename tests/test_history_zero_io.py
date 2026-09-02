"""Zero remote I/O proofs for governance history."""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from conftest_history import (
    object_json,
    write_authority_yaml,
    write_observations,
    write_sample_snapshot,
)
from governance.cli import main
from governance.history import append_history_entry


def test_history_in_process_no_remote_calls(
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

    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    history = tmp_path / "history.json"
    assert (
        main(
            [
                "history",
                "add",
                "--history",
                str(history),
                "--snapshot",
                "a.json",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "history",
                "add",
                "--history",
                str(history),
                "--snapshot",
                "b.json",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "history",
                "show",
                "--history",
                str(history),
                "--object",
                object_json(),
            ]
        )
        == 0
    )
    assert main(["history", "inspect", "--history", str(history)]) == 0
    assert calls == []


def test_history_full_context_under_bombs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    write_authority_yaml(tmp_path / "auth.yaml")
    history = tmp_path / "history.json"
    assert (
        main(
            [
                "history",
                "add",
                "--history",
                str(history),
                "--snapshot",
                "a.json",
                "--observations",
                "obs.json",
                "--authority",
                "auth.yaml",
            ]
        )
        == 0
    )
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_b.json")
    assert (
        main(
            [
                "history",
                "add",
                "--history",
                str(history),
                "--snapshot",
                "b.json",
                "--observations",
                "obs_b.json",
                "--authority",
                "auth.yaml",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "history",
                "show",
                "--history",
                str(history),
                "--object",
                object_json(),
                "--governance-object",
                '{"namespace":"acme.commerce","kind":"table","logical_id":"orders","parent":null}',
                "--property",
                "/description",
            ]
        )
        == 0
    )
    assert calls == []


def test_history_subprocess_offline_smoke(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    env = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "COLLIBRA_MODE"}}
    env.pop("COLLIBRA_BASE_URL", None)
    env.pop("COLLIBRA_CLIENT_ID", None)
    env.pop("COLLIBRA_CLIENT_SECRET", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "governance",
            "history",
            "inspect",
            "--history",
            str(history),
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
    assert '"history_schema": "governance-history"' in result.stdout


def test_history_module_import_boundary() -> None:
    import governance.history as history
    import governance.history.append as append_mod
    import governance.history.evolution as evolution_mod

    for module in (history, append_mod, evolution_mod):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "governance.scanner" not in source
        assert "governance.integrations" not in source
        assert "governance.reconciliation" not in source
