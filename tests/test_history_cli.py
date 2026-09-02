"""CLI tests for governance history."""

from __future__ import annotations

import json
from pathlib import Path

from conftest_history import (
    build_observation_set,
    governance_object_json,
    object_json,
    write_authority_yaml,
    write_observations,
    write_sample_snapshot,
)
from governance.cli import main
from governance.history import append_history_entry, canonical_history_json, load_history_artifact


def test_history_add_first_human(tmp_path: Path, capsys) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    code = main(
        [
            "history",
            "add",
            "--history",
            str(history),
            "--snapshot",
            str(tmp_path / "a.json"),
            "--label",
            "env=dev",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "entries=1" in out
    assert "writes=1" in out
    assert history.is_file()


def test_history_add_json(tmp_path: Path, capsys) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    code = main(
        [
            "history",
            "add",
            "--history",
            str(history),
            "--snapshot",
            "a.json",
            "--format",
            "json",
        ]
    )
    assert code == 0
    stdout = capsys.readouterr().out
    loaded = load_history_artifact(history)
    assert stdout == canonical_history_json(loaded)


def test_history_add_duplicate_exit_4(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    before = history.read_bytes()
    code = main(["history", "add", "--history", str(history), "--snapshot", "a.json"])
    assert code == 4
    assert history.read_bytes() == before


def test_history_show_missing_selector_exit_2() -> None:
    assert main(["history", "show", "--history", "x.json"]) == 2


def test_history_show_and_inspect(tmp_path: Path, capsys) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="updated")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    append_history_entry(history, snapshot_path="b.json")
    code = main(
        [
            "history",
            "show",
            "--history",
            str(history),
            "--object",
            object_json(),
            "--format",
            "json",
            "--output",
            str(tmp_path / "evo.json"),
        ]
    )
    assert code == 0
    assert (tmp_path / "evo.json").is_file()
    payload = json.loads(capsys.readouterr().out)
    assert payload["writes_performed"] == 0

    code = main(["history", "inspect", "--history", str(history), "--format", "json"])
    assert code == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["history_schema"] == "governance-history"
    assert len(inspected["entries"]) == 2


def test_history_show_governance_object(tmp_path: Path) -> None:
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
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_b.json")
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth.yaml"],
    )
    assert (
        main(
            [
                "history",
                "show",
                "--history",
                str(history),
                "--governance-object",
                governance_object_json(),
                "--property",
                "/description",
            ]
        )
        == 0
    )


def test_history_bad_label_exit_2(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    code = main(
        [
            "history",
            "add",
            "--history",
            str(tmp_path / "h.json"),
            "--snapshot",
            "a.json",
            "--label",
            "nocolon",
        ]
    )
    assert code == 2


def test_history_usage_missing_subcommand_exit_2() -> None:
    assert main(["history"]) == 2


def test_history_add_align_none_uses_stored(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json", source_name="dev", database_name="dev_db")
    write_sample_snapshot(
        tmp_path / "b.json",
        description="x",
        source_name="prod",
        database_name="prod_db",
    )
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
                "--align-source-roots",
                "--align-database-roots",
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


def test_duplicate_label_keys_exit_2(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    assert (
        main(
            [
                "history",
                "add",
                "--history",
                str(tmp_path / "h.json"),
                "--snapshot",
                "a.json",
                "--label",
                "env=dev",
                "--label",
                " env =prod",
            ]
        )
        == 2
    )


def test_env_vs_Env_labels_distinct_ok(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    assert (
        main(
            [
                "history",
                "add",
                "--history",
                str(tmp_path / "h.json"),
                "--snapshot",
                "a.json",
                "--label",
                "Env=dev",
                "--label",
                "env=prod",
            ]
        )
        == 0
    )


def test_equals_in_label_value_ok(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    assert (
        main(
            [
                "history",
                "add",
                "--history",
                str(tmp_path / "h.json"),
                "--snapshot",
                "a.json",
                "--label",
                "ref=feature/x=1",
            ]
        )
        == 0
    )
    loaded = load_history_artifact(tmp_path / "h.json")
    assert loaded.entries[0].state.labels == {"ref": "feature/x=1"}


def test_credential_label_keys_rejected_cli(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    for key in ("token", "TOKEN", "client_secret"):
        assert (
            main(
                [
                    "history",
                    "add",
                    "--history",
                    str(tmp_path / f"h-{key}.json"),
                    "--snapshot",
                    "a.json",
                    "--label",
                    f"{key}=secret",
                ]
            )
            == 2
        )


def test_environment_token_value_ok(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    assert (
        main(
            [
                "history",
                "add",
                "--history",
                str(tmp_path / "h.json"),
                "--snapshot",
                "a.json",
                "--label",
                "environment=token",
            ]
        )
        == 0
    )


def test_absolute_sibling_paths_store_relative(tmp_path: Path) -> None:
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    write_sample_snapshot(snaps / "a.json")
    write_observations(snaps / "obs.json")
    write_authority_yaml(snaps / "auth.yaml")
    history = tmp_path / "hist" / "history.json"
    history.parent.mkdir()
    code = main(
        [
            "history",
            "add",
            "--history",
            str(history),
            "--snapshot",
            str((snaps / "a.json").resolve()),
            "--observations",
            str((snaps / "obs.json").resolve()),
            "--authority",
            str((snaps / "auth.yaml").resolve()),
        ]
    )
    assert code == 0
    loaded = load_history_artifact(history)
    assert loaded.entries[0].operator.snapshot_path.startswith("../")
    assert ".." in loaded.entries[0].operator.observations_path  # type: ignore[operator]


def test_query_strict_extra_key_exit_2(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    append_history_entry(history, snapshot_path="b.json")
    assert (
        main(
            [
                "history",
                "show",
                "--history",
                str(history),
                "--object",
                '{"kind":"table","path":["sales","orders"],"extra":1}',
            ]
        )
        == 2
    )


def test_query_strict_numeric_namespace_exit_2(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    append_history_entry(history, snapshot_path="b.json")
    assert (
        main(
            [
                "history",
                "show",
                "--history",
                str(history),
                "--governance-object",
                '{"namespace":1,"kind":"table","logical_id":"orders","parent":null}',
            ]
        )
        == 2
    )


def test_query_strict_numeric_path_segment_exit_2(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    append_history_entry(history, snapshot_path="b.json")
    assert (
        main(
            [
                "history",
                "show",
                "--history",
                str(history),
                "--object",
                '{"kind":"table","path":["sales",1]}',
            ]
        )
        == 2
    )


def test_query_strict_path_string_exit_2(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    append_history_entry(history, snapshot_path="b.json")
    assert (
        main(
            [
                "history",
                "show",
                "--history",
                str(history),
                "--object",
                '{"kind":"table","path":"sales/orders"}',
            ]
        )
        == 2
    )


def test_query_strict_duplicate_json_key_exit_2(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    append_history_entry(history, snapshot_path="b.json")
    assert (
        main(
            [
                "history",
                "show",
                "--history",
                str(history),
                "--object",
                '{"kind":"table","kind":"schema","path":["sales","orders"]}',
            ]
        )
        == 2
    )


def test_query_strict_recursion_error_exit_2_never_exit_1(tmp_path: Path, monkeypatch) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    append_history_entry(history, snapshot_path="b.json")

    def boom(*args, **kwargs):
        raise RecursionError("too deep")

    monkeypatch.setattr("governance.cli.json.loads", boom)
    code = main(
        [
            "history",
            "show",
            "--history",
            str(history),
            "--object",
            object_json(),
        ]
    )
    assert code == 2


def test_valid_contract_transformation_governance_object_passes(tmp_path: Path) -> None:
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
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_b.json", build_observation_set(value="x"))
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth.yaml"],
    )
    assert (
        main(
            [
                "history",
                "show",
                "--history",
                str(history),
                "--governance-object",
                governance_object_json(),
                "--property",
                "/description",
            ]
        )
        == 0
    )


def test_output_collision_with_inputs_fails_byte_identical(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    append_history_entry(history, snapshot_path="b.json")
    before_hist = history.read_bytes()
    before_snap = (tmp_path / "a.json").read_bytes()
    code = main(
        [
            "history",
            "show",
            "--history",
            str(history),
            "--object",
            object_json(),
            "--output",
            str(history),
        ]
    )
    assert code == 4
    assert history.read_bytes() == before_hist
    assert (tmp_path / "a.json").read_bytes() == before_snap


def test_preexisting_output_sentinel_unchanged_on_validation_failure(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    # Only one entry -> show may fail object evolution needing 2? Actually need 2 for transitions.
    # Use invalid object so failure happens before write.
    sentinel = tmp_path / "out.json"
    sentinel.write_text("SENTINEL\n", encoding="utf-8")
    code = main(
        [
            "history",
            "show",
            "--history",
            str(history),
            "--object",
            '{"kind":"table","path":["missing","nope"]}',
            "--output",
            str(sentinel),
        ]
    )
    assert code == 4
    assert sentinel.read_text(encoding="utf-8") == "SENTINEL\n"


def test_nul_path_inspect_exit_4(tmp_path: Path, capsys) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    payload = json.loads(history.read_text(encoding="utf-8"))
    payload["entries"][0]["operator"]["snapshot_path"] = "a\x00.json"
    history.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    assert main(["history", "inspect", "--history", str(history), "--format", "json"]) == 4
    assert str(tmp_path) not in capsys.readouterr().out


def test_forged_credential_label_rejected(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json", labels={"env": "dev"})
    payload = json.loads(history.read_text(encoding="utf-8"))
    payload["entries"][0]["state"]["labels"] = {"TOKEN": "leak"}
    history.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    assert main(["history", "inspect", "--history", str(history)]) == 4


def test_inspect_full_context_observations_schema_recursion_exit_4(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """CLI inspect must map observations schema RecursionError to exit 4 (no traceback)."""
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
    before = history.read_bytes()

    class _BoomValidator:
        def iter_errors(self, _payload):
            raise RecursionError("too deep")

    monkeypatch.setattr(
        "governance.observations.artifact.Draft202012Validator",
        lambda _schema: _BoomValidator(),
    )
    code = main(["history", "inspect", "--history", str(history)])
    captured = capsys.readouterr()
    assert code == 4
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert history.read_bytes() == before
