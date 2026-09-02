"""Strict history load and full resolve tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest_history import (
    history_entry_for_snapshot,
    write_history_direct,
    write_sample_snapshot,
)
from governance.cli import main
from governance.history import (
    HistoryError,
    append_history_entry,
    load_history_artifact,
    resolve_history_artifacts,
)


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(HistoryError) as exc_info:
        load_history_artifact(tmp_path / "missing.json")
    assert exc_info.value.errors[0].code == "history_read_error"


def test_resolve_scanner_incompatible_timeline(tmp_path: Path) -> None:
    a = write_sample_snapshot(tmp_path / "a.json", scanner="postgresql")
    b = write_sample_snapshot(tmp_path / "b.json", description="x", scanner="other")
    history_path = tmp_path / "history.json"
    write_history_direct(
        history_path,
        entries=[
            history_entry_for_snapshot(a, "a.json"),
            history_entry_for_snapshot(b, "b.json"),
        ],
    )
    history = load_history_artifact(history_path)
    with pytest.raises(HistoryError) as exc_info:
        resolve_history_artifacts(history, history_path)
    assert any("scanner" in err.path or "scanner" in err.code for err in exc_info.value.errors)


def test_inspect_scanner_incompatible_exit_4(tmp_path: Path) -> None:
    a = write_sample_snapshot(tmp_path / "a.json", scanner="postgresql")
    b = write_sample_snapshot(tmp_path / "b.json", description="x", scanner="other")
    history_path = tmp_path / "history.json"
    write_history_direct(
        history_path,
        entries=[
            history_entry_for_snapshot(a, "a.json"),
            history_entry_for_snapshot(b, "b.json"),
        ],
    )
    assert main(["history", "inspect", "--history", str(history_path)]) == 4


def test_inspect_system_type_mismatch_exit_4(tmp_path: Path) -> None:
    a = write_sample_snapshot(tmp_path / "a.json", system_type="postgresql")
    b = write_sample_snapshot(
        tmp_path / "b.json",
        description="x",
        system_type="mysql",
    )
    history_path = tmp_path / "history.json"
    write_history_direct(
        history_path,
        entries=[
            history_entry_for_snapshot(a, "a.json"),
            history_entry_for_snapshot(b, "b.json"),
        ],
    )
    assert main(["history", "inspect", "--history", str(history_path)]) == 4


def test_inspect_root_mismatch_without_ack_exit_4(tmp_path: Path) -> None:
    a = write_sample_snapshot(tmp_path / "a.json", source_name="dev", database_name="dev_db")
    b = write_sample_snapshot(
        tmp_path / "b.json",
        description="x",
        source_name="prod",
        database_name="prod_db",
    )
    history_path = tmp_path / "history.json"
    write_history_direct(
        history_path,
        entries=[
            history_entry_for_snapshot(a, "a.json"),
            history_entry_for_snapshot(b, "b.json"),
        ],
        align_source_roots=False,
        align_database_roots=False,
    )
    assert main(["history", "inspect", "--history", str(history_path)]) == 4


def test_inspect_root_mismatch_with_ack_succeeds(tmp_path: Path) -> None:
    a = write_sample_snapshot(tmp_path / "a.json", source_name="dev", database_name="dev_db")
    b = write_sample_snapshot(
        tmp_path / "b.json",
        description="x",
        source_name="prod",
        database_name="prod_db",
    )
    history_path = tmp_path / "history.json"
    write_history_direct(
        history_path,
        entries=[
            history_entry_for_snapshot(a, "a.json"),
            history_entry_for_snapshot(b, "b.json"),
        ],
        align_source_roots=True,
        align_database_roots=True,
    )
    assert main(["history", "inspect", "--history", str(history_path)]) == 0


def test_add_fails_when_old_adjacency_incompatible(tmp_path: Path) -> None:
    a = write_sample_snapshot(tmp_path / "a.json", scanner="postgresql")
    b = write_sample_snapshot(tmp_path / "b.json", description="x", scanner="other")
    write_sample_snapshot(tmp_path / "c.json", description="y", scanner="postgresql")
    history_path = tmp_path / "history.json"
    write_history_direct(
        history_path,
        entries=[
            history_entry_for_snapshot(a, "a.json"),
            history_entry_for_snapshot(b, "b.json"),
        ],
    )
    before = history_path.read_bytes()
    with pytest.raises(HistoryError):
        append_history_entry(history_path, snapshot_path="c.json")
    assert history_path.read_bytes() == before


def test_recursion_error_in_schema_iter_errors_maps_to_invalid_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history_path = tmp_path / "history.json"
    append_history_entry(history_path, snapshot_path="a.json")

    class _BoomValidator:
        def iter_errors(self, _payload):
            raise RecursionError("too deep")

    monkeypatch.setattr(
        "governance.history.load._get_validator",
        lambda: _BoomValidator(),
    )
    with pytest.raises(HistoryError) as exc_info:
        load_history_artifact(history_path)
    assert exc_info.value.errors[0].code == "invalid_history_artifact"
    assert "too deeply nested" in exc_info.value.errors[0].message


def test_schema_iter_errors_recursion_cli_exit_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history_path = tmp_path / "history.json"
    append_history_entry(history_path, snapshot_path="a.json")

    class _BoomValidator:
        def iter_errors(self, _payload):
            raise RecursionError("too deep")

    monkeypatch.setattr(
        "governance.history.load._get_validator",
        lambda: _BoomValidator(),
    )
    assert main(["history", "inspect", "--history", str(history_path)]) == 4
