"""History artifact model / serialize / identity tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest_history import write_authority_yaml, write_observations, write_sample_snapshot
from governance.history import (
    HISTORY_SCHEMA,
    HISTORY_VERSION,
    ComparisonPolicy,
    GovernanceHistory,
    HistoryError,
    append_history_entry,
    canonical_history_json,
    load_history_artifact,
)
from governance.history.models import HistoryEntry, HistoryOperator


def test_history_round_trip_bytes(tmp_path: Path) -> None:
    snap = write_sample_snapshot(tmp_path / "a.json")
    history_path = tmp_path / "history.json"
    append_history_entry(history_path, snapshot_path="a.json")
    loaded = load_history_artifact(history_path)
    assert (
        loaded.content_identity()
        == GovernanceHistory(
            comparison_policy=ComparisonPolicy(False, False),
            entries=loaded.entries,
        ).content_identity()
    )
    assert history_path.read_text(encoding="utf-8") == canonical_history_json(loaded)
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    assert payload["history_schema"] == HISTORY_SCHEMA
    assert payload["history_version"] == HISTORY_VERSION
    assert payload["entries"][0]["state"]["snapshot"] == snap.content_identity().to_dict()


def test_operator_and_captured_at_excluded_from_identity(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history_path = tmp_path / "history.json"
    append_history_entry(
        history_path,
        snapshot_path="a.json",
        captured_at="2024-01-01T00:00:00Z",
        labels={"env": "dev"},
    )
    first = load_history_artifact(history_path)
    # Rewrite with different operator path spelling + captured_at but same state
    entry = first.entries[0]
    altered = GovernanceHistory(
        comparison_policy=first.comparison_policy,
        entries=(
            HistoryEntry(
                state=entry.state,
                operator=HistoryOperator(snapshot_path="./a.json"),
                captured_at="2025-06-01T12:00:00.123Z",
            ),
        ),
    )
    # Normalize path via append semantics: forge with same relative after normalize
    # Identity ignores operator/captured_at entirely.
    assert altered.content_identity() == first.content_identity()


def test_same_semantic_history_different_operator_path_same_identity(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    h1 = tmp_path / "h1.json"
    h2 = tmp_path / "nested"
    h2.mkdir()
    # Copy snapshots beside nested history using relative ../ paths
    import shutil

    shutil.copy(tmp_path / "a.json", h2 / "a.json")
    shutil.copy(tmp_path / "b.json", h2 / "b.json")
    append_history_entry(h1, snapshot_path="a.json", labels={"env": "dev"})
    append_history_entry(h1, snapshot_path="b.json", labels={"env": "dev"})
    append_history_entry(h2 / "history.json", snapshot_path="a.json", labels={"env": "dev"})
    append_history_entry(h2 / "history.json", snapshot_path="b.json", labels={"env": "dev"})
    assert (
        load_history_artifact(h1).content_identity()
        == load_history_artifact(h2 / "history.json").content_identity()
    )


def test_authority_input_path_order_same_identities(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    write_authority_yaml(tmp_path / "auth_a.yaml", rule_id="rule-a")
    write_authority_yaml(
        tmp_path / "auth_b.yaml",
        rule_id="rule-b",
        kind="column",
        property_pointer="/name",
        provider_type="dbt",
        source_ref="model.x",
    )
    h1 = tmp_path / "h1.json"
    h2 = tmp_path / "h2.json"
    append_history_entry(
        h1,
        snapshot_path="a.json",
        observations_path="obs.json",
        authority_paths=["auth_a.yaml", "auth_b.yaml"],
    )
    append_history_entry(
        h2,
        snapshot_path="a.json",
        observations_path="obs.json",
        authority_paths=["auth_b.yaml", "auth_a.yaml"],
    )
    assert (
        load_history_artifact(h1).content_identity() == load_history_artifact(h2).content_identity()
    )
    assert (
        load_history_artifact(h1).entries[0].state.context
        == load_history_artifact(h2).entries[0].state.context
    )


def test_labels_material_to_identity(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    path_a = tmp_path / "ha.json"
    path_b = tmp_path / "hb.json"
    append_history_entry(path_a, snapshot_path="a.json", labels={"env": "dev"})
    append_history_entry(path_b, snapshot_path="a.json", labels={"env": "prod"})
    assert (
        load_history_artifact(path_a).content_identity()
        != load_history_artifact(path_b).content_identity()
    )


def test_envelope_token_rejected(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history_path = tmp_path / "history.json"
    append_history_entry(history_path, snapshot_path="a.json")
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    payload["token"] = "secret"
    history_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(HistoryError) as exc_info:
        load_history_artifact(history_path)
    assert any(err.code == "invalid_history_artifact" for err in exc_info.value.errors)


def test_integrity_mismatch(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history_path = tmp_path / "history.json"
    append_history_entry(history_path, snapshot_path="a.json")
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    payload["content_identity"]["digest"] = "0" * 64
    history_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(HistoryError) as exc_info:
        load_history_artifact(history_path)
    assert exc_info.value.errors[0].code == "history_integrity_mismatch"


def test_unsupported_schema_version(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history_path = tmp_path / "history.json"
    append_history_entry(history_path, snapshot_path="a.json")
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    payload["history_version"] = "99"
    history_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(HistoryError) as exc_info:
        load_history_artifact(history_path)
    assert exc_info.value.errors[0].code == "unsupported_history_version"


def test_label_schema_rejects_reserved_keys_case_variants() -> None:
    from importlib.resources import files

    from jsonschema import Draft202012Validator

    text = (
        files("governance.history.schemas")
        .joinpath("governance-history.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    assert "(?i)" not in text
    schema = json.loads(text)
    labels_validator = Draft202012Validator(schema["$defs"]["labels"])
    assert labels_validator.is_valid({"environment": "token"})
    assert labels_validator.is_valid({"Env": "dev"})
    for key in ("TOKEN", "token", "Password", "api_key", "SECRET"):
        assert not labels_validator.is_valid({key: "leak"})


def test_normalize_labels_casefold_still_rejects_reserved() -> None:
    from governance.history.models import normalize_labels

    with pytest.raises(HistoryError):
        normalize_labels({"TOKEN": "x"})
    with pytest.raises(HistoryError):
        normalize_labels({"Password": "x"})
    assert normalize_labels({"environment": "token"}) == {"environment": "token"}
