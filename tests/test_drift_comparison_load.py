"""Trust-boundary tests for comparison artifact loading used by drift."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from conftest_drift import (
    inject_forged_property_change,
    sort_object_changes,
    write_aligned_root_comparison,
    write_different_description_comparison,
    write_identical_comparison,
    write_rehashed_comparison,
)
from governance.comparison.load import (
    ComparisonArtifactError,
    load_comparison_artifact,
    validate_comparison_result_semantics,
)
from governance.exporters.inventory import SCANNER_CONTRACT_VERSION
from governance.identity.hashing import snapshot_comparison_identity


def _assert_error_code(exc: ComparisonArtifactError, code: str) -> None:
    assert any(item.code == code for item in exc.errors)


def test_load_valid_comparison_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_identical_comparison(artifact)
    loaded = load_comparison_artifact(artifact)
    assert loaded["status"] == "identical"
    assert loaded["comparison_schema"] == "governance-snapshot-comparison"


def test_forged_content_identity_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_identical_comparison(artifact)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["content_identity"] = {
        "algorithm": "sha256",
        "hashing_contract_version": "1",
        "digest": "0" * 64,
    }
    artifact.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "integrity_mismatch")


def test_duplicate_object_identity_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_different_description_comparison(artifact)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    duplicate = copy.deepcopy(payload["object_changes"][0])
    payload["object_changes"].append(duplicate)
    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = snapshot_comparison_identity(without_identity).to_dict()
    artifact.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "invalid_artifact")
    assert any("duplicate object identity" in item.message for item in exc.value.errors)


def test_wrong_parent_identity_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_different_description_comparison(artifact)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["object_identity"]["kind"] == "table":
            change["parent_identity"] = {"kind": "database", "path": []}
            break
    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = snapshot_comparison_identity(without_identity).to_dict()
    artifact.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "invalid_artifact")
    assert any(
        "parent identity" in item.message or "schema validation" in item.message
        for item in exc.value.errors
    )


def test_duplicate_property_pointer_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_different_description_comparison(artifact)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["change"] == "changed" and change["property_changes"]:
            prop = copy.deepcopy(change["property_changes"][0])
            change["property_changes"].append(prop)
            break
    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = snapshot_comparison_identity(without_identity).to_dict()
    artifact.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "invalid_artifact")
    assert any("duplicate property pointer" in item.message for item in exc.value.errors)


def test_non_standard_json_literal_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    artifact.write_text(
        '{"comparison_schema": "governance-snapshot-comparison", "value": 1e999}\n',
        encoding="utf-8",
    )
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "parse_error")


def test_invalid_property_pointer_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_different_description_comparison(artifact)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["change"] == "changed" and change["property_changes"]:
            change["property_changes"][0]["property"] = "description"
            break
    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = snapshot_comparison_identity(without_identity).to_dict()
    artifact.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "invalid_artifact")
    assert any(
        "property pointer" in item.message or "schema validation" in item.message
        for item in exc.value.errors
    )


def test_both_sides_missing_not_materially_different(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_different_description_comparison(artifact)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["change"] == "changed" and change["property_changes"]:
            change["property_changes"][0]["baseline"] = {"has_value": False}
            change["property_changes"][0]["candidate"] = {"has_value": False}
            break
    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = snapshot_comparison_identity(without_identity).to_dict()
    artifact.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "invalid_artifact")
    assert any(
        "not materially different" in item.message
        or "not compatible with comparison producer contract" in item.message
        for item in exc.value.errors
    )


def test_kind_property_incompatibility_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_different_description_comparison(artifact)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["object_identity"]["kind"] == "table":
            change["property_changes"] = [
                {
                    "property": "/system_type",
                    "baseline": {"has_value": False},
                    "candidate": {"has_value": True, "value": "x"},
                }
            ]
            break
    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = snapshot_comparison_identity(without_identity).to_dict()
    artifact.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "invalid_artifact")
    assert any("not compatible with object kind" in item.message for item in exc.value.errors)


def test_validate_semantics_directly_on_valid_result(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    result = write_identical_comparison(artifact)
    validate_comparison_result_semantics(result)


def test_missing_comparison_file_read_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(missing)
    _assert_error_code(exc.value, "read_error")


def _load_forged(tmp_path: Path, payload: dict) -> None:
    artifact = tmp_path / "cmp.json"
    write_rehashed_comparison(artifact, payload)
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(artifact)
    _assert_error_code(exc.value, "invalid_artifact")


def test_pairwise_system_type_mismatch_rejected(tmp_path: Path) -> None:
    write_identical_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["candidate"]["system_type"] = "mysql"
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(tmp_path / "cmp.json")
    assert any(item.path == "/candidate/system_type" for item in exc.value.errors)


def test_pairwise_scanner_mismatch_rejected(tmp_path: Path) -> None:
    write_identical_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["candidate"]["scanner"] = "other-scanner"
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(tmp_path / "cmp.json")
    assert any(item.path == "/candidate/scanner" for item in exc.value.errors)


def test_unsupported_baseline_scanner_contract_version_rejected(tmp_path: Path) -> None:
    write_identical_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["baseline"]["scanner_contract_version"] = "999"
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(tmp_path / "cmp.json")
    assert any(item.path == "/baseline/scanner_contract_version" for item in exc.value.errors)


def test_unsupported_candidate_scanner_contract_version_rejected(tmp_path: Path) -> None:
    write_identical_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["candidate"]["scanner_contract_version"] = "999"
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(tmp_path / "cmp.json")
    assert any(item.path == "/candidate/scanner_contract_version" for item in exc.value.errors)


def test_differing_source_names_with_null_alignment_rejected(tmp_path: Path) -> None:
    write_aligned_root_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["root_alignment"]["source"] = None
    _load_forged(tmp_path, payload)


def test_differing_database_names_with_null_alignment_rejected(tmp_path: Path) -> None:
    write_aligned_root_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["root_alignment"]["database"] = None
    _load_forged(tmp_path, payload)


def test_wrong_source_alignment_values_rejected(tmp_path: Path) -> None:
    write_aligned_root_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["root_alignment"]["source"] = {"baseline": "dev", "candidate": "wrong"}
    _load_forged(tmp_path, payload)


def test_redundant_source_alignment_when_names_equal_rejected(tmp_path: Path) -> None:
    write_identical_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["root_alignment"]["source"] = {
        "baseline": payload["baseline"]["source_name"],
        "candidate": payload["candidate"]["source_name"],
    }
    _load_forged(tmp_path, payload)


def test_root_data_source_added_rejected(tmp_path: Path) -> None:
    write_identical_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["status"] = "different"
    payload["summary"]["added"] = 1
    payload["object_changes"] = [
        {
            "change": "added",
            "object_identity": {"kind": "data_source", "path": []},
            "parent_identity": None,
            "property_changes": [],
        }
    ]
    _load_forged(tmp_path, payload)


def test_root_database_removed_rejected(tmp_path: Path) -> None:
    write_identical_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["status"] = "different"
    payload["summary"]["removed"] = 1
    payload["object_changes"] = [
        {
            "change": "removed",
            "object_identity": {"kind": "database", "path": []},
            "parent_identity": {"kind": "data_source", "path": []},
            "property_changes": [],
        }
    ]
    _load_forged(tmp_path, payload)


def test_source_name_differs_but_name_change_omitted_rejected(tmp_path: Path) -> None:
    write_aligned_root_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["object_changes"] = [
        change
        for change in payload["object_changes"]
        if not (
            change["object_identity"]["kind"] == "data_source" and change["change"] == "changed"
        )
    ]
    payload["summary"]["changed"] = len(
        [item for item in payload["object_changes"] if item["change"] == "changed"]
    )
    payload["summary"]["property_changes"] = sum(
        len(item["property_changes"])
        for item in payload["object_changes"]
        if item["change"] == "changed"
    )
    _load_forged(tmp_path, payload)


def test_name_values_disagree_with_metadata_rejected(tmp_path: Path) -> None:
    write_aligned_root_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["object_identity"]["kind"] == "data_source" and change["change"] == "changed":
            for prop in change["property_changes"]:
                if prop["property"] == "/name":
                    prop["candidate"]["value"] = "wrong-name"
    _load_forged(tmp_path, payload)


def test_forged_data_source_system_type_change_rejected(tmp_path: Path) -> None:
    write_different_description_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["object_changes"].append(
        {
            "change": "changed",
            "object_identity": {"kind": "data_source", "path": []},
            "parent_identity": None,
            "property_changes": [
                {
                    "property": "/system_type",
                    "baseline": {"has_value": True, "value": "postgresql"},
                    "candidate": {"has_value": True, "value": "mysql"},
                }
            ],
        }
    )
    payload["summary"]["changed"] += 1
    payload["summary"]["property_changes"] += 1
    payload["status"] = "different"
    _load_forged(tmp_path, payload)


def test_exact_real_aligned_comparison_accepted(tmp_path: Path) -> None:
    artifact = tmp_path / "cmp.json"
    write_aligned_root_comparison(artifact)
    loaded = load_comparison_artifact(artifact)
    assert loaded["root_alignment"]["source"]["baseline"] == "dev"
    assert loaded["baseline"]["scanner_contract_version"] == SCANNER_CONTRACT_VERSION


def test_wrong_parent_path_rejected(tmp_path: Path) -> None:
    write_different_description_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["object_identity"]["kind"] == "table":
            change["parent_identity"] = {"kind": "schema", "path": ["wrong"]}
            break
    _load_forged(tmp_path, payload)


def test_malformed_property_pointer_a_tilde2b_rejected(tmp_path: Path) -> None:
    write_different_description_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["change"] == "changed" and change["property_changes"]:
            change["property_changes"][0]["property"] = "/a~2b"
            break
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_malformed_property_pointer_a_tilde_rejected(tmp_path: Path) -> None:
    write_different_description_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["change"] == "changed" and change["property_changes"]:
            change["property_changes"][0]["property"] = "/a~"
            break
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_equal_reordered_dict_values_rejected(tmp_path: Path) -> None:
    write_different_description_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["change"] == "changed" and change["property_changes"]:
            baseline = change["property_changes"][0]["baseline"]
            candidate = change["property_changes"][0]["candidate"]
            if baseline.get("has_value") and isinstance(baseline.get("value"), dict):
                change["property_changes"][0]["baseline"] = dict(
                    reversed(baseline["value"].items())
                )
                change["property_changes"][0]["candidate"] = dict(
                    reversed(candidate["value"].items())
                )
            else:
                change["property_changes"][0]["baseline"] = {
                    "has_value": True,
                    "value": {"b": 1, "a": 2},
                }
                change["property_changes"][0]["candidate"] = {
                    "has_value": True,
                    "value": {"a": 2, "b": 1},
                }
            break
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(tmp_path / "cmp.json")
    assert any("not materially different" in item.message for item in exc.value.errors)


def test_false_vs_zero_technical_attribute_accepted(tmp_path: Path) -> None:
    from conftest_drift import write_technical_attribute_comparison

    write_technical_attribute_comparison(
        tmp_path / "cmp.json",
        baseline_attrs={"typed": False},
        candidate_attrs={"typed": 0},
    )
    loaded = load_comparison_artifact(tmp_path / "cmp.json")
    assert loaded["status"] == "different"
    pointers = {
        prop["property"]
        for change in loaded["object_changes"]
        if change["change"] == "changed"
        for prop in change["property_changes"]
    }
    assert "/technical_attributes/typed" in pointers


def test_missing_vs_null_technical_attribute_accepted(tmp_path: Path) -> None:
    from conftest_drift import write_technical_attribute_comparison

    write_technical_attribute_comparison(
        tmp_path / "cmp.json",
        baseline_attrs={},
        candidate_attrs={"nullable_key": None},
    )
    loaded = load_comparison_artifact(tmp_path / "cmp.json")
    assert loaded["status"] == "different"
    change = next(
        item
        for item in loaded["object_changes"]
        if item["change"] == "changed"
        and any(
            prop["property"] == "/technical_attributes/nullable_key"
            for prop in item["property_changes"]
        )
    )
    prop = next(
        item
        for item in change["property_changes"]
        if item["property"] == "/technical_attributes/nullable_key"
    )
    assert prop["baseline"] == {"has_value": False}
    assert prop["candidate"] == {"has_value": True, "value": None}


def test_nullable_false_to_zero_forged_rejected(tmp_path: Path) -> None:
    inject_forged_property_change(
        tmp_path / "cmp.json",
        kind="column",
        identity_path=["sales", "orders", "id"],
        parent_identity={"kind": "table", "path": ["sales", "orders"]},
        property_pointer="/nullable",
        baseline={"has_value": True, "value": False},
        candidate={"has_value": True, "value": 0},
    )
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(tmp_path / "cmp.json")
    assert any(
        "not compatible with comparison producer contract" in item.message
        for item in exc.value.errors
    )


def test_fixed_description_missing_to_null_forged_rejected(tmp_path: Path) -> None:
    write_different_description_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["change"] == "changed" and change["property_changes"]:
            change["property_changes"][0]["baseline"] = {"has_value": False}
            change["property_changes"][0]["candidate"] = {"has_value": True, "value": None}
            break
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(tmp_path / "cmp.json")
    assert any(
        "not compatible with comparison producer contract" in item.message
        for item in exc.value.errors
    )


def test_forged_table_name_change_rejected(tmp_path: Path) -> None:
    write_different_description_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    for change in payload["object_changes"]:
        if change["object_identity"]["kind"] == "table":
            change["property_changes"] = [
                {
                    "property": "/name",
                    "baseline": {"has_value": True, "value": "orders"},
                    "candidate": {"has_value": True, "value": "orders_v2"},
                }
            ]
            break
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_forged_schema_name_change_rejected(tmp_path: Path) -> None:
    write_different_description_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["object_changes"].append(
        {
            "change": "changed",
            "object_identity": {"kind": "schema", "path": ["sales"]},
            "parent_identity": {"kind": "database", "path": []},
            "property_changes": [
                {
                    "property": "/name",
                    "baseline": {"has_value": True, "value": "sales"},
                    "candidate": {"has_value": True, "value": "sales_v2"},
                }
            ],
        }
    )
    payload["summary"]["changed"] += 1
    payload["summary"]["property_changes"] += 1
    payload["status"] = "different"
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError):
        load_comparison_artifact(tmp_path / "cmp.json")


def test_added_table_removed_column_rejected(tmp_path: Path) -> None:
    write_identical_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["status"] = "different"
    payload["summary"] = {
        "added": 1,
        "removed": 1,
        "changed": 0,
        "unchanged": 0,
        "property_changes": 0,
    }
    payload["object_changes"] = [
        {
            "change": "added",
            "object_identity": {"kind": "table", "path": ["sales", "new_table"]},
            "parent_identity": {"kind": "schema", "path": ["sales"]},
            "property_changes": [],
        },
        {
            "change": "removed",
            "object_identity": {"kind": "column", "path": ["sales", "new_table", "id"]},
            "parent_identity": {"kind": "table", "path": ["sales", "new_table"]},
            "property_changes": [],
        },
    ]
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    with pytest.raises(ComparisonArtifactError) as exc:
        load_comparison_artifact(tmp_path / "cmp.json")
    assert any("hierarchy is inconsistent" in item.message for item in exc.value.errors)


def test_added_table_added_column_valid_shape(tmp_path: Path) -> None:
    write_identical_comparison(tmp_path / "cmp.json")
    payload = json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))
    payload["status"] = "different"
    payload["summary"] = {
        "added": 2,
        "removed": 0,
        "changed": 0,
        "unchanged": 0,
        "property_changes": 0,
    }
    payload["object_changes"] = [
        {
            "change": "added",
            "object_identity": {"kind": "column", "path": ["sales", "new_table", "id"]},
            "parent_identity": {"kind": "table", "path": ["sales", "new_table"]},
            "property_changes": [],
        },
        {
            "change": "added",
            "object_identity": {"kind": "table", "path": ["sales", "new_table"]},
            "parent_identity": {"kind": "schema", "path": ["sales"]},
            "property_changes": [],
        },
    ]
    sort_object_changes(payload)
    write_rehashed_comparison(tmp_path / "cmp.json", payload)
    loaded = load_comparison_artifact(tmp_path / "cmp.json")
    assert loaded["summary"]["added"] == 2
