"""History context coherence tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest_history import (
    build_observation_set,
    write_authority_yaml,
    write_observations,
    write_sample_snapshot,
)
from governance.cli import main
from governance.domain.authority import NormalizedAuthorityPolicySet
from governance.history import (
    HistoryError,
    append_history_entry,
    load_history_artifact,
    resolve_history_artifacts,
)
from governance.history.context import build_context_identities
from governance.history.models import GovernanceHistory, HistoryEntry, HistoryEntryState
from governance.history.serialize import write_history_artifact


def test_observations_only_context(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    history = tmp_path / "history.json"
    updated = append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs.json",
    )
    assert updated.entries[0].state.context_form() == "provenance"
    resolved = resolve_history_artifacts(updated, history)
    assert resolved.states[0].observations is not None
    assert resolved.states[0].authority is None
    assert resolved.states[0].conflicts is None


def test_full_context_derives_conflicts(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    write_authority_yaml(tmp_path / "auth.yaml")
    history = tmp_path / "history.json"
    updated = append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs.json",
        authority_paths=["auth.yaml"],
    )
    assert updated.entries[0].state.context_form() == "full"
    resolved = resolve_history_artifacts(updated, history)
    assert resolved.states[0].conflicts is not None


def test_tampered_conflicts_digest_rejected(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    write_authority_yaml(tmp_path / "auth.yaml")
    history = tmp_path / "history.json"
    updated = append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs.json",
        authority_paths=["auth.yaml"],
    )
    entry = updated.entries[0]
    assert entry.state.context is not None
    tampered_context = dict(entry.state.context)
    conflicts = dict(tampered_context["conflicts"])
    conflicts["digest"] = "f" * 64
    tampered_context["conflicts"] = conflicts
    forged = GovernanceHistory(
        comparison_policy=updated.comparison_policy,
        entries=(
            HistoryEntry(
                state=HistoryEntryState(
                    snapshot=entry.state.snapshot,
                    labels=entry.state.labels,
                    context=tampered_context,
                ),
                operator=entry.operator,
                captured_at=entry.captured_at,
            ),
        ),
    )
    write_history_artifact(forged, history)
    loaded = load_history_artifact(history)
    with pytest.raises(HistoryError) as exc_info:
        resolve_history_artifacts(loaded, history)
    assert any(err.code == "context_integrity_mismatch" for err in exc_info.value.errors)


def test_modified_observations_file_mismatch(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json", observations_path="obs.json")
    write_observations(tmp_path / "obs.json", build_observation_set(value="changed"))
    loaded = load_history_artifact(history)
    with pytest.raises(HistoryError) as exc_info:
        resolve_history_artifacts(loaded, history)
    assert any(err.code == "context_integrity_mismatch" for err in exc_info.value.errors)


def test_build_context_identities_authority_requires_observations() -> None:
    with pytest.raises(HistoryError):
        build_context_identities(authority=NormalizedAuthorityPolicySet())


def test_modified_authority_file_context_identity_mismatch(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    write_authority_yaml(tmp_path / "auth.yaml", rule_id="rule-a")
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs.json",
        authority_paths=["auth.yaml"],
    )
    write_authority_yaml(tmp_path / "auth.yaml", rule_id="rule-b", kind="column")
    loaded = load_history_artifact(history)
    with pytest.raises(HistoryError) as exc_info:
        resolve_history_artifacts(loaded, history)
    assert any(err.code == "context_integrity_mismatch" for err in exc_info.value.errors)


def test_missing_old_observations_exit_4(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json", observations_path="obs.json")
    (tmp_path / "obs.json").unlink()
    assert main(["history", "inspect", "--history", str(history)]) == 4
    write_sample_snapshot(tmp_path / "b.json", description="x")
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
        == 4
    )
    assert (
        main(
            [
                "history",
                "show",
                "--history",
                str(history),
                "--object",
                '{"kind":"table","path":["sales","orders"]}',
            ]
        )
        == 4
    )


def test_missing_old_authority_exit_4(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    write_authority_yaml(tmp_path / "auth.yaml")
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs.json",
        authority_paths=["auth.yaml"],
    )
    (tmp_path / "auth.yaml").unlink()
    assert main(["history", "inspect", "--history", str(history)]) == 4


def test_recomputed_conflict_mismatch(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    write_authority_yaml(tmp_path / "auth.yaml")
    history = tmp_path / "history.json"
    updated = append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs.json",
        authority_paths=["auth.yaml"],
    )
    entry = updated.entries[0]
    assert entry.state.context is not None
    tampered_context = dict(entry.state.context)
    conflicts = dict(tampered_context["conflicts"])
    conflicts["digest"] = "a" * 64
    tampered_context["conflicts"] = conflicts
    forged = GovernanceHistory(
        comparison_policy=updated.comparison_policy,
        entries=(
            HistoryEntry(
                state=HistoryEntryState(
                    snapshot=entry.state.snapshot,
                    labels=entry.state.labels,
                    context=tampered_context,
                ),
                operator=entry.operator,
                captured_at=entry.captured_at,
            ),
        ),
    )
    write_history_artifact(forged, history)
    with pytest.raises(HistoryError) as exc_info:
        resolve_history_artifacts(load_history_artifact(history), history)
    assert any(err.code == "context_integrity_mismatch" for err in exc_info.value.errors)


def test_provenance_only_availability_distinctions(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_a.json")
    write_observations(tmp_path / "obs_b.json", build_observation_set(value="x"))
    write_authority_yaml(tmp_path / "auth.yaml")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json", observations_path="obs_a.json")
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    from governance.domain.graph import GraphNodeIdentity
    from governance.history import build_history_evolution

    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    avail = result["transitions"][0]["availability"]
    assert avail["provenance"] == {"baseline": True, "candidate": True}
    assert avail["authority_decision"] == {"baseline": False, "candidate": True}
    assert avail["conflict"] == {"baseline": False, "candidate": True}


def test_nul_in_snapshot_path_inspect_exit_4(tmp_path: Path, capsys) -> None:
    import json

    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    payload = json.loads(history.read_text(encoding="utf-8"))
    payload["entries"][0]["operator"]["snapshot_path"] = "a\x00.json"
    history.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    code = main(["history", "inspect", "--history", str(history), "--format", "json"])
    assert code == 4
    out = capsys.readouterr().out + capsys.readouterr().err
    assert str(tmp_path) not in out


def test_nul_in_observations_and_authority_paths_exit_4(tmp_path: Path, capsys) -> None:
    import json

    write_sample_snapshot(tmp_path / "a.json")
    write_observations(tmp_path / "obs.json")
    write_authority_yaml(tmp_path / "auth.yaml")
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs.json",
        authority_paths=["auth.yaml"],
    )
    payload = json.loads(history.read_text(encoding="utf-8"))
    payload["entries"][0]["operator"]["observations_path"] = "obs\x00.json"
    history.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert main(["history", "inspect", "--history", str(history), "--format", "json"]) == 4
    assert str(tmp_path) not in capsys.readouterr().out

    append_history_entry(
        tmp_path / "h2.json",
        snapshot_path="a.json",
        observations_path="obs.json",
        authority_paths=["auth.yaml"],
    )
    payload2 = json.loads((tmp_path / "h2.json").read_text(encoding="utf-8"))
    payload2["entries"][0]["operator"]["authority_paths"] = ["auth\x00.yaml"]
    (tmp_path / "h2.json").write_text(
        json.dumps(payload2, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assert (
        main(["history", "inspect", "--history", str(tmp_path / "h2.json"), "--format", "json"])
        == 4
    )


def test_resolve_failure_mapped_exit_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    append_history_entry(history, snapshot_path="b.json")

    def boom(self):
        raise OSError("symlink loop")

    monkeypatch.setattr(Path, "resolve", boom)
    code = main(
        [
            "history",
            "show",
            "--history",
            str(history),
            "--object",
            '{"kind":"table","path":["sales","orders"]}',
            "--output",
            str(tmp_path / "out.json"),
            "--format",
            "json",
        ]
    )
    assert code == 4
    assert str(tmp_path) not in capsys.readouterr().out
