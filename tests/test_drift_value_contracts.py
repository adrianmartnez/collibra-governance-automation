"""Value-shape and contract regression tests for drift/comparison."""

from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema
import pytest
from jsonschema import Draft202012Validator

from conftest_comparison import build_snapshot
from conftest_drift import (
    inject_forged_property_change,
    write_different_description_comparison,
    write_full_description_policy,
    write_identical_comparison,
    write_policy,
    write_rehashed_comparison,
)
from governance.comparison import build_comparison_result
from governance.comparison.load import ComparisonArtifactError, load_comparison_artifact
from governance.drift.errors import CODE_INVALID_POLICY, DriftError
from governance.drift.load import load_drift_policy
from governance.drift.policy import parse_and_normalize_policy
from governance.drift.result import build_drift_result


def _drift_result_schema() -> dict:
    from importlib.resources import files

    text = (
        files("governance.drift.schemas")
        .joinpath("governance-drift-result.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def _forged_column_property(tmp_path: Path, property_pointer: str, baseline, candidate) -> dict:
    return inject_forged_property_change(
        tmp_path / "cmp.json",
        kind="column",
        identity_path=["sales", "orders", "id"],
        parent_identity={"kind": "table", "path": ["sales", "orders"]},
        property_pointer=property_pointer,
        baseline=baseline,
        candidate=candidate,
    )


@pytest.mark.parametrize(
    ("baseline", "candidate"),
    [
        ({"has_value": True, "value": "yes"}, {"has_value": True, "value": False}),
        ({"has_value": True, "value": True}, {"has_value": True, "value": 1}),
    ],
)
def test_nullable_non_bool_values_rejected(tmp_path: Path, baseline, candidate) -> None:
    _forged_column_property(tmp_path, "/nullable", baseline, candidate)
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_ordinal_bool_rejected(tmp_path: Path) -> None:
    _forged_column_property(
        tmp_path,
        "/ordinal_position",
        {"has_value": True, "value": 1},
        {"has_value": True, "value": True},
    )
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_ordinal_zero_rejected(tmp_path: Path) -> None:
    _forged_column_property(
        tmp_path,
        "/ordinal_position",
        {"has_value": True, "value": 1},
        {"has_value": True, "value": 0},
    )
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_data_type_non_string_rejected(tmp_path: Path) -> None:
    _forged_column_property(
        tmp_path,
        "/data_type",
        {"has_value": True, "value": "integer"},
        {"has_value": True, "value": 123},
    )
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_fixed_description_has_value_false_rejected(tmp_path: Path) -> None:
    write_different_description_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["change"] == "changed" and change["property_changes"]:
            change["property_changes"][0]["baseline"] = {"has_value": False}
            change["property_changes"][0]["candidate"] = {"has_value": True, "value": "x"}
            break
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_added_data_source_policy_rejected() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "bad-root",
                "match": {
                    "change": "added",
                    "object": {"kind": "data_source", "path": []},
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert all(item.code == CODE_INVALID_POLICY for item in exc.value.errors)


def test_removed_database_policy_rejected() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "bad-root",
                "match": {
                    "change": "removed",
                    "object": {"kind": "database", "path": []},
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert all(item.code == CODE_INVALID_POLICY for item in exc.value.errors)


def test_added_schema_policy_valid() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "allow-schema",
                "match": {
                    "change": "added",
                    "object": {"kind": "schema", "path": ["sales"]},
                },
            }
        ],
    }
    policy = parse_and_normalize_policy(document)
    assert policy.rules[0].id == "allow-schema"


def test_nullable_string_expectation_invalid_policy() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "bad-nullable",
                "match": {
                    "change": "changed",
                    "object": {"kind": "column", "path": ["sales", "orders", "id"]},
                    "property": "/nullable",
                },
                "expected": {
                    "candidate": {"has_value": True, "value": "yes"},
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert any(
        "not compatible with comparison producer contract" in item.message
        for item in exc.value.errors
    )


def test_nullable_missing_expectation_invalid_policy() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "bad-nullable",
                "match": {
                    "change": "changed",
                    "object": {"kind": "column", "path": ["sales", "orders", "id"]},
                    "property": "/nullable",
                },
                "expected": {
                    "baseline": {"has_value": False},
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert any(
        "not compatible with comparison producer contract" in item.message
        for item in exc.value.errors
    )


def test_technical_attribute_missing_expectation_valid_policy() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "allow-missing",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/technical_attributes/flag",
                },
                "expected": {
                    "baseline": {"has_value": False},
                },
            }
        ],
    }
    policy = parse_and_normalize_policy(document)
    assert policy.rules[0].expected == {"baseline": {"has_value": False}}


def test_equal_expected_sides_invalid_policy() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "noop",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/description",
                },
                "expected": {
                    "baseline": {"has_value": True, "value": None},
                    "candidate": {"has_value": True, "value": None},
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert any("must not be equal" in item.message for item in exc.value.errors)


def test_table_name_policy_rule_invalid() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "bad-name",
                "match": {
                    "change": "changed",
                    "object": {"kind": "table", "path": ["sales", "orders"]},
                    "property": "/name",
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert any("not compatible" in item.message for item in exc.value.errors)


def test_full_expected_difference_result_status(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_full_description_policy(policy_path)
    policy = load_drift_policy(policy_path)
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
    )
    result = build_drift_result(comparison, policy)
    assert result["status"] == "expected_difference"
    assert result["summary"]["unexpected_drift"] == 0
    assert all(
        item["classification"] == "expected_difference" for item in result["classified_changes"]
    )


def test_policy_only_literal_not_copied_to_result(tmp_path: Path) -> None:
    secret = "POLICY_ONLY_LITERAL_XYZ_98765"
    write_policy(
        tmp_path / "policy.yaml",
        f"""drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: allow-description
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
    expected:
      candidate:
        has_value: true
        value: "{secret}"
""",
    )
    policy = load_drift_policy(tmp_path / "policy.yaml")
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="other-value"),
    )
    result = build_drift_result(comparison, policy)
    encoded = json.dumps(result)
    assert secret not in encoded


def test_credential_keys_rejected_without_echo(tmp_path: Path) -> None:
    secret = "super-secret-value"
    write_policy(
        tmp_path / "policy.yaml",
        f"""drift_schema: governance-drift-policy
drift_version: "1"
password: {secret}
rules: []
""",
    )
    with pytest.raises(DriftError) as exc:
        load_drift_policy(tmp_path / "policy.yaml")
    assert all(secret not in item.message for item in exc.value.errors)


def test_result_schema_rejects_null_policy_on_expected_difference(tmp_path: Path) -> None:
    write_full_description_policy(tmp_path / "policy.yaml")
    policy = load_drift_policy(tmp_path / "policy.yaml")
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
    )
    result = build_drift_result(comparison, policy)
    result["drift_policy_identity"] = None
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_policy_identity_same_with_different_mtime(tmp_path: Path) -> None:
    from governance.identity.hashing import drift_policy_identity

    content = """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: allow-description
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
"""
    path_a = tmp_path / "a.yaml"
    path_b = tmp_path / "b.yaml"
    write_policy(path_a, content)
    write_policy(path_b, content + "# comment\n")
    os.utime(path_a, (1_000, 1_000))
    os.utime(path_b, (9_000, 9_000))
    identity_a = drift_policy_identity(load_drift_policy(path_a).to_identity_dict()).digest
    identity_b = drift_policy_identity(load_drift_policy(path_b).to_identity_dict()).digest
    assert identity_a == identity_b


def test_equivalent_policy_files_produce_identical_drift_bytes(tmp_path: Path) -> None:
    from governance.drift import canonical_drift_json

    policy_a = tmp_path / "a.yaml"
    policy_b = tmp_path / "b.yaml"
    write_full_description_policy(policy_a)
    write_policy(
        policy_b,
        """# reordered
drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: allow-table-description
    match:
      change: changed
      object:
        kind: table
        path: ["sales", "orders"]
      property: /description
  - id: allow-source-description
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
""",
    )
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
    )
    first = build_drift_result(comparison, load_drift_policy(policy_a))
    second = build_drift_result(comparison, load_drift_policy(policy_b))
    assert canonical_drift_json(first) == canonical_drift_json(second)
    assert first["content_identity"] == second["content_identity"]


def _forged_pk_property(tmp_path: Path, property_pointer: str, baseline, candidate) -> dict:
    return inject_forged_property_change(
        tmp_path / "cmp.json",
        kind="primary_key",
        identity_path=["sales", "orders", "orders_pkey"],
        parent_identity={"kind": "table", "path": ["sales", "orders"]},
        property_pointer=property_pointer,
        baseline=baseline,
        candidate=candidate,
    )


def _forged_fk_property(tmp_path: Path, property_pointer: str, baseline, candidate) -> dict:
    return inject_forged_property_change(
        tmp_path / "cmp.json",
        kind="foreign_key",
        identity_path=["sales", "orders", "orders_id_fkey"],
        parent_identity={"kind": "table", "path": ["sales", "orders"]},
        property_pointer=property_pointer,
        baseline=baseline,
        candidate=candidate,
    )


def _forged_relationship_property(
    tmp_path: Path, property_pointer: str, baseline, candidate
) -> dict:
    return inject_forged_property_change(
        tmp_path / "cmp.json",
        kind="relationship",
        identity_path=["sales", "orders", "orders_id_fkey"],
        parent_identity={"kind": "table", "path": ["sales", "orders"]},
        property_pointer=property_pointer,
        baseline=baseline,
        candidate=candidate,
    )


def test_pk_column_ids_empty_rejected(tmp_path: Path) -> None:
    _forged_pk_property(
        tmp_path,
        "/column_ids",
        {"has_value": True, "value": [{"kind": "column", "path": ["sales", "orders", "id"]}]},
        {"has_value": True, "value": []},
    )
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_pk_column_ids_wrong_kind_rejected(tmp_path: Path) -> None:
    _forged_pk_property(
        tmp_path,
        "/column_ids",
        {"has_value": True, "value": [{"kind": "column", "path": ["sales", "orders", "id"]}]},
        {"has_value": True, "value": [{"kind": "table", "path": ["sales", "orders"]}]},
    )
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_pk_column_ids_wrong_table_prefix_rejected(tmp_path: Path) -> None:
    _forged_pk_property(
        tmp_path,
        "/column_ids",
        {"has_value": True, "value": [{"kind": "column", "path": ["sales", "orders", "id"]}]},
        {"has_value": True, "value": [{"kind": "column", "path": ["sales", "customers", "id"]}]},
    )
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_fk_referenced_table_id_wrong_kind_rejected(tmp_path: Path) -> None:
    _forged_fk_property(
        tmp_path,
        "/referenced_table_id",
        {"has_value": True, "value": {"kind": "table", "path": ["sales", "customers"]}},
        {"has_value": True, "value": {"kind": "column", "path": ["sales", "customers", "id"]}},
    )
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_fk_referenced_column_ids_mixed_prefix_rejected(tmp_path: Path) -> None:
    _forged_fk_property(
        tmp_path,
        "/referenced_column_ids",
        {
            "has_value": True,
            "value": [{"kind": "column", "path": ["sales", "customers", "id"]}],
        },
        {
            "has_value": True,
            "value": [
                {"kind": "column", "path": ["sales", "customers", "id"]},
                {"kind": "column", "path": ["sales", "orders", "id"]},
            ],
        },
    )
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_relationship_foreign_key_id_wrong_kind_rejected(tmp_path: Path) -> None:
    _forged_relationship_property(
        tmp_path,
        "/foreign_key_id",
        {
            "has_value": True,
            "value": {"kind": "foreign_key", "path": ["sales", "orders", "orders_id_fkey"]},
        },
        {"has_value": True, "value": {"kind": "table", "path": ["sales", "orders"]}},
    )
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_relationship_foreign_key_id_prefix_mismatch_rejected(tmp_path: Path) -> None:
    _forged_relationship_property(
        tmp_path,
        "/foreign_key_id",
        {
            "has_value": True,
            "value": {"kind": "foreign_key", "path": ["sales", "orders", "orders_id_fkey"]},
        },
        {
            "has_value": True,
            "value": {"kind": "foreign_key", "path": ["sales", "customers", "customers_id_fkey"]},
        },
    )
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_valid_ownership_null_and_object_accepted(tmp_path: Path) -> None:
    write_identical_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["status"] = "different"
    payload["summary"] = {
        "added": 0,
        "removed": 0,
        "changed": 1,
        "unchanged": 0,
        "property_changes": 1,
    }
    payload["object_changes"] = [
        {
            "change": "changed",
            "object_identity": {"kind": "data_source", "path": []},
            "parent_identity": None,
            "property_changes": [
                {
                    "property": "/ownership",
                    "baseline": {"has_value": True, "value": None},
                    "candidate": {
                        "has_value": True,
                        "value": {"owner_name": "team-a", "owner_type": "role"},
                    },
                }
            ],
        }
    ]
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    loaded = load_comparison_artifact(tmp_path / "cmp.json")
    assert loaded["status"] == "different"


def test_malformed_ownership_object_rejected(tmp_path: Path) -> None:
    write_identical_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["status"] = "different"
    payload["summary"] = {
        "added": 0,
        "removed": 0,
        "changed": 1,
        "unchanged": 0,
        "property_changes": 1,
    }
    payload["object_changes"] = [
        {
            "change": "changed",
            "object_identity": {"kind": "data_source", "path": []},
            "parent_identity": None,
            "property_changes": [
                {
                    "property": "/ownership",
                    "baseline": {"has_value": True, "value": None},
                    "candidate": {
                        "has_value": True,
                        "value": {"owner_name": "team-a", "extra": "x"},
                    },
                }
            ],
        }
    ]
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")
