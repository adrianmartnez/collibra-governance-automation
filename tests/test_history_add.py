"""History append / ADD flow tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest_history import (
    build_observation_set,
    write_authority_yaml,
    write_observations,
    write_sample_snapshot,
)
from governance.history import (
    HistoryError,
    append_history_entry,
    load_history_artifact,
)
from governance.history.errors import CODE_DUPLICATE_HISTORY_STATE


def test_first_and_multiple_add(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="updated")
    history_path = tmp_path / "history.json"
    first = append_history_entry(history_path, snapshot_path="a.json")
    assert len(first.entries) == 1
    second = append_history_entry(history_path, snapshot_path="b.json")
    assert len(second.entries) == 2
    loaded = load_history_artifact(history_path)
    assert len(loaded.entries) == 2


def test_adjacent_duplicate_rejected(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history_path = tmp_path / "history.json"
    append_history_entry(history_path, snapshot_path="a.json")
    before = history_path.read_bytes()
    with pytest.raises(HistoryError) as exc_info:
        append_history_entry(history_path, snapshot_path="a.json")
    assert any(err.code == CODE_DUPLICATE_HISTORY_STATE for err in exc_info.value.errors)
    assert history_path.read_bytes() == before


def test_aba_allowed(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="mid")
    history_path = tmp_path / "history.json"
    append_history_entry(history_path, snapshot_path="a.json")
    append_history_entry(history_path, snapshot_path="b.json")
    third = append_history_entry(history_path, snapshot_path="a.json")
    assert len(third.entries) == 3
    assert third.entries[0].state.snapshot == third.entries[2].state.snapshot


def test_operator_coupling_authority_requires_observations(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_authority_yaml(tmp_path / "auth.yaml")
    history_path = tmp_path / "history.json"
    with pytest.raises(HistoryError) as exc_info:
        append_history_entry(
            history_path,
            snapshot_path="a.json",
            authority_paths=["auth.yaml"],
        )
    assert any("authority requires observations" in err.message for err in exc_info.value.errors)


def test_full_context_add(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    write_authority_yaml(tmp_path / "auth.yaml")
    history_path = tmp_path / "history.json"
    updated = append_history_entry(
        history_path,
        snapshot_path="a.json",
        observations_path="obs.json",
        authority_paths=["auth.yaml"],
    )
    context = updated.entries[0].state.context
    assert context is not None
    assert set(context) == {"observations", "authority", "conflicts"}
    assert updated.entries[0].operator.observations_path == "obs.json"
    assert updated.entries[0].operator.authority_paths == ("auth.yaml",)


def test_provenance_only_add(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json", build_observation_set())
    history_path = tmp_path / "history.json"
    updated = append_history_entry(
        history_path,
        snapshot_path="a.json",
        observations_path="obs.json",
    )
    context = updated.entries[0].state.context
    assert context is not None
    assert set(context) == {"observations"}
    assert updated.entries[0].operator.authority_paths is None


def test_align_flags_seed_and_conflict(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json", source_name="dev", database_name="dev_db")
    write_sample_snapshot(
        tmp_path / "b.json",
        description="x",
        source_name="prod",
        database_name="prod_db",
    )
    history_path = tmp_path / "history.json"
    append_history_entry(
        history_path,
        snapshot_path="a.json",
        align_source_roots=True,
        align_database_roots=True,
    )
    loaded = load_history_artifact(history_path)
    assert loaded.comparison_policy.align_source_roots is True
    # Subsequent omit uses stored policy
    append_history_entry(history_path, snapshot_path="b.json")
    # Conflicting explicit True against stored False is rejected
    write_sample_snapshot(tmp_path / "c.json")
    write_sample_snapshot(tmp_path / "d.json", description="d")
    history2 = tmp_path / "h2.json"
    append_history_entry(history2, snapshot_path="c.json")
    with pytest.raises(HistoryError):
        append_history_entry(
            history2,
            snapshot_path="d.json",
            align_source_roots=True,
        )


def test_captured_at_formats(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history_path = tmp_path / "history.json"
    append_history_entry(
        history_path,
        snapshot_path="a.json",
        captured_at="2024-01-02T03:04:05Z",
    )
    assert load_history_artifact(history_path).entries[0].captured_at == "2024-01-02T03:04:05Z"

    write_sample_snapshot(tmp_path / "b.json", description="x")
    history_b = tmp_path / "hb.json"
    append_history_entry(
        history_b,
        snapshot_path="b.json",
        captured_at="2024-01-02T03:04:05.123456Z",
    )
    with pytest.raises(HistoryError):
        append_history_entry(
            tmp_path / "bad.json",
            snapshot_path="a.json",
            captured_at="2024-01-02T03:04:05+00:00",
        )
    with pytest.raises(HistoryError):
        append_history_entry(
            tmp_path / "bad2.json",
            snapshot_path="a.json",
            captured_at="2024-01-02T03:04:05",
        )


@pytest.mark.parametrize(
    "value",
    [
        "2024-01-02T03:04:05.1Z",
        "2024-01-02T03:04:05.123456Z",
        "2024-01-02T03:04:05.123456789Z",
        "2024-01-02T03:04:05." + ("9" * 40) + "Z",
    ],
)
def test_captured_at_fractional_arbitrary_precision(tmp_path: Path, value: str) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history_path = tmp_path / "history.json"
    append_history_entry(history_path, snapshot_path="a.json", captured_at=value)
    assert load_history_artifact(history_path).entries[0].captured_at == value


def test_captured_at_bad_calendar_date_rejected(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    with pytest.raises(HistoryError):
        append_history_entry(
            tmp_path / "bad.json",
            snapshot_path="a.json",
            captured_at="2024-13-40T03:04:05Z",
        )
    with pytest.raises(HistoryError):
        append_history_entry(
            tmp_path / "bad2.json",
            snapshot_path="a.json",
            captured_at="2024-02-30T03:04:05.123Z",
        )


def test_align_flags_reject_non_bool_coercion(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history_path = tmp_path / "history.json"
    with pytest.raises(HistoryError) as exc_info:
        append_history_entry(
            history_path,
            snapshot_path="a.json",
            align_source_roots="false",  # type: ignore[arg-type]
        )
    assert any("boolean" in err.message for err in exc_info.value.errors)
    with pytest.raises(HistoryError):
        append_history_entry(
            history_path,
            snapshot_path="a.json",
            align_source_roots=0,  # type: ignore[arg-type]
        )
    with pytest.raises(HistoryError):
        append_history_entry(
            history_path,
            snapshot_path="a.json",
            align_database_roots=1,  # type: ignore[arg-type]
        )
    append_history_entry(
        history_path,
        snapshot_path="a.json",
        align_source_roots=True,
        align_database_roots=False,
    )
    write_sample_snapshot(tmp_path / "b.json", description="x")
    append_history_entry(history_path, snapshot_path="b.json", align_source_roots=None)
    loaded = load_history_artifact(history_path)
    assert loaded.comparison_policy.align_source_roots is True
    assert loaded.comparison_policy.align_database_roots is False


def test_authority_paths_string_rejected(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    with pytest.raises(HistoryError) as exc_info:
        append_history_entry(
            tmp_path / "history.json",
            snapshot_path="a.json",
            observations_path="obs.json",
            authority_paths="x.yaml",  # type: ignore[arg-type]
        )
    assert any(
        err.path == "/operator/authority_paths" and "sequence" in err.message
        for err in exc_info.value.errors
    )
