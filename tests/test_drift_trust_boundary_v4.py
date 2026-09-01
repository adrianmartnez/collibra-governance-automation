"""Trust-boundary v4 regression tests for issue #77."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest_drift import (
    inject_forged_property_change,
    write_different_description_comparison,
    write_identical_comparison,
)
from governance.cli import main
from governance.comparison.load import (
    ComparisonArtifactError,
    load_comparison_artifact,
    validate_comparison_result_semantics,
)
from governance.drift.errors import CODE_INVALID_POLICY, DriftError
from governance.drift.policy import parse_and_normalize_policy
from governance.identity.hashing import snapshot_comparison_identity


def _assert_error_code(exc: ComparisonArtifactError, code: str) -> None:
    assert any(item.code == code for item in exc.errors)


def _inject_duplicate_key(text: str, key: str, earlier_json_value: str) -> str:
    needle = f'"{key}":'
    index = text.index(needle)
    return text[:index] + f'  "{key}": {earlier_json_value},\n' + text[index:]


def _minimal_semantics_payload(tmp_path: Path) -> dict:
    write_identical_comparison(tmp_path / "cmp.json")
    return json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))


def test_embedded_table_identity_string_path_rejected(tmp_path: Path) -> None:
    inject_forged_property_change(
        tmp_path / "cmp.json",
        kind="foreign_key",
        identity_path=["sales", "orders", "orders_id_fkey"],
        parent_identity={"kind": "table", "path": ["sales", "orders"]},
        property_pointer="/referenced_table_id",
        baseline={"has_value": True, "value": {"kind": "table", "path": ["sales", "customers"]}},
        candidate={"has_value": True, "value": {"kind": "table", "path": "ab"}},
    )
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(tmp_path / "cmp.json")
    _assert_error_code(exc.value, "invalid_artifact")


def test_embedded_column_identity_string_path_rejected(tmp_path: Path) -> None:
    inject_forged_property_change(
        tmp_path / "cmp.json",
        kind="primary_key",
        identity_path=["sales", "orders", "orders_pkey"],
        parent_identity={"kind": "table", "path": ["sales", "orders"]},
        property_pointer="/column_ids",
        baseline={
            "has_value": True,
            "value": [{"kind": "column", "path": ["sales", "orders", "id"]}],
        },
        candidate={
            "has_value": True,
            "value": [{"kind": "column", "path": "ab"}],
        },
    )
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_embedded_list_identity_paths_still_accepted(tmp_path: Path) -> None:
    inject_forged_property_change(
        tmp_path / "cmp.json",
        kind="foreign_key",
        identity_path=["sales", "orders", "orders_id_fkey"],
        parent_identity={"kind": "table", "path": ["sales", "orders"]},
        property_pointer="/referenced_table_id",
        baseline={"has_value": True, "value": {"kind": "table", "path": ["sales", "customers"]}},
        candidate={"has_value": True, "value": {"kind": "table", "path": ["sales", "orders"]}},
    )
    loaded = load_comparison_artifact(tmp_path / "cmp.json")
    assert loaded["status"] == "different"


def test_policy_expected_string_path_identity_rejected() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "bad-ref",
                "match": {
                    "change": "changed",
                    "object": {
                        "kind": "foreign_key",
                        "path": ["sales", "orders", "orders_id_fkey"],
                    },
                    "property": "/referenced_table_id",
                },
                "expected": {
                    "candidate": {
                        "has_value": True,
                        "value": {"kind": "table", "path": "ab"},
                    }
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert all(item.code == CODE_INVALID_POLICY for item in exc.value.errors)
    assert any(
        "not compatible with comparison producer contract" in item.message
        for item in exc.value.errors
    )


def test_semantics_rejects_non_string_path_segments(tmp_path: Path) -> None:
    payload = _minimal_semantics_payload(tmp_path)
    payload["object_changes"] = [
        {
            "change": "added",
            "object_identity": {"kind": "schema", "path": [123]},
            "parent_identity": {"kind": "database", "path": []},
            "property_changes": [],
        }
    ]
    with pytest.raises(ComparisonArtifactError):
        validate_comparison_result_semantics(payload)


def test_semantics_rejects_string_object_path(tmp_path: Path) -> None:
    payload = _minimal_semantics_payload(tmp_path)
    payload["object_changes"] = [
        {
            "change": "added",
            "object_identity": {"kind": "table", "path": "ab"},
            "parent_identity": {"kind": "schema", "path": ["sales"]},
            "property_changes": [],
        }
    ]
    with pytest.raises(ComparisonArtifactError):
        validate_comparison_result_semantics(payload)


def test_semantics_rejects_string_parent_path(tmp_path: Path) -> None:
    payload = _minimal_semantics_payload(tmp_path)
    payload["object_changes"] = [
        {
            "change": "added",
            "object_identity": {"kind": "column", "path": ["sales", "orders", "id"]},
            "parent_identity": {"kind": "table", "path": "ab"},
            "property_changes": [],
        }
    ]
    with pytest.raises(ComparisonArtifactError):
        validate_comparison_result_semantics(payload)


def test_semantics_accepts_normal_list_identities(tmp_path: Path) -> None:
    payload = _minimal_semantics_payload(tmp_path)
    payload["status"] = "different"
    payload["summary"] = {
        "added": 1,
        "removed": 0,
        "changed": 0,
        "unchanged": 0,
        "property_changes": 0,
    }
    payload["object_changes"] = [
        {
            "change": "added",
            "object_identity": {"kind": "column", "path": ["sales", "orders", "id"]},
            "parent_identity": {"kind": "table", "path": ["sales", "orders"]},
            "property_changes": [],
        }
    ]
    validate_comparison_result_semantics(payload)


def test_duplicate_root_json_key_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_identical_comparison(artifact)
    text = artifact.read_text(encoding="utf-8")
    artifact.write_text(_inject_duplicate_key(text, "status", '"different"'), encoding="utf-8")
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "parse_error")
    assert all(item.message == "invalid comparison JSON" for item in exc.value.errors)


def test_duplicate_nested_content_identity_key_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_identical_comparison(artifact)
    text = artifact.read_text(encoding="utf-8")
    patched = _inject_duplicate_key(text, "algorithm", '"md5"')
    artifact.write_text(patched, encoding="utf-8")
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "parse_error")


def test_duplicate_nested_baseline_key_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_identical_comparison(artifact)
    text = artifact.read_text(encoding="utf-8")
    patched = _inject_duplicate_key(text, "scanner", '"forged-scanner"')
    artifact.write_text(patched, encoding="utf-8")
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "parse_error")


def test_content_identity_full_equality_required(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_identical_comparison(artifact)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    expected = snapshot_comparison_identity(without_identity).to_dict()
    payload["content_identity"] = {**expected, "digest": "0" * 64}
    artifact.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "integrity_mismatch")


def test_invalid_utf8_comparison_cli_exit_4(tmp_path: Path, capsys) -> None:
    comparison = tmp_path / "cmp.json"
    comparison.write_bytes(b"\xff\xfe\xfa")
    code = main(["drift", "--comparison", str(comparison), "--format", "json"])
    captured = capsys.readouterr()
    assert code == 4
    payload = json.loads(captured.out)
    assert payload["errors"][0]["code"] == "comparison_parse_error"
    assert str(tmp_path) not in captured.err


def test_invalid_utf8_policy_cli_exit_4(tmp_path: Path, capsys) -> None:
    comparison = tmp_path / "cmp.json"
    policy = tmp_path / "policy.yaml"
    write_different_description_comparison(comparison)
    policy.write_bytes(b"\xff\xfe")
    code = main(
        [
            "drift",
            "--comparison",
            str(comparison),
            "--policy",
            str(policy),
            "--format",
            "json",
        ]
    )
    assert code == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["code"] == "policy_parse_error"
    assert str(tmp_path) not in payload["errors"][0]["message"]


def test_recursion_error_in_comparison_json_load_maps_to_parse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_identical_comparison(tmp_path / "cmp.json")

    def _boom(*args, **kwargs):
        raise RecursionError("too deep")

    monkeypatch.setattr("governance.comparison.load.json.loads", _boom)
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(tmp_path / "cmp.json")
    _assert_error_code(exc.value, "parse_error")


def test_recursion_error_in_comparison_validate_json_maps_to_parse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_identical_comparison(tmp_path / "cmp.json")

    real_loads = json.loads

    def _loads_with_validate_boom(*args, **kwargs):
        payload = real_loads(*args, **kwargs)
        monkeypatch.setattr(
            "governance.comparison.load.validate_json_value",
            lambda _value: (_ for _ in ()).throw(RecursionError("too deep")),
        )
        return payload

    monkeypatch.setattr("governance.comparison.load.json.loads", _loads_with_validate_boom)
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(tmp_path / "cmp.json")
    _assert_error_code(exc.value, "parse_error")


def test_recursion_error_in_policy_yaml_load_maps_to_policy_parse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from governance.drift.load import load_drift_policy

    policy = tmp_path / "policy.yaml"
    policy.write_text(
        'drift_schema: governance-drift-policy\ndrift_version: "1"\nrules: []\n',
        encoding="utf-8",
    )

    def _boom(_text: str):
        raise RecursionError("too deep")

    monkeypatch.setattr("governance.drift.load.load_drift_policy_yaml", _boom)
    with pytest.raises(DriftError) as exc:
        load_drift_policy(policy)
    assert exc.value.errors[0].code == "policy_parse_error"
