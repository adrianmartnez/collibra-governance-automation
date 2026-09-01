"""JSON Schema validation for drift artifacts and diagnostics."""

from __future__ import annotations

import json
from importlib.resources import files

import jsonschema
import pytest
from jsonschema import Draft202012Validator

from conftest_comparison import build_snapshot
from conftest_drift import write_policy
from governance.comparison import build_comparison_result
from governance.drift.errors import DiagnosticError, drift_diagnostics_failure
from governance.drift.load import load_drift_policy
from governance.drift.result import build_drift_result


def _drift_result_schema() -> dict:
    text = (
        files("governance.drift.schemas")
        .joinpath("governance-drift-result.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def _drift_diagnostics_schema() -> dict:
    text = (
        files("governance.drift.schemas")
        .joinpath("governance-drift-diagnostics.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def test_valid_no_difference_result_accepted() -> None:
    comparison = build_comparison_result(build_snapshot(), build_snapshot())
    result = build_drift_result(comparison, None)
    Draft202012Validator(_drift_result_schema()).validate(result)


def test_valid_expected_difference_result_accepted(tmp_path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_policy(
        policy_path,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: allow-description
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
""",
    )
    policy = load_drift_policy(policy_path)
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
    )
    result = build_drift_result(comparison, policy)
    Draft202012Validator(_drift_result_schema()).validate(result)


def test_extra_root_field_rejected() -> None:
    comparison = build_comparison_result(build_snapshot(), build_snapshot())
    result = build_drift_result(comparison, None)
    result["extra"] = True
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_invalid_status_rejected() -> None:
    comparison = build_comparison_result(build_snapshot(), build_snapshot())
    result = build_drift_result(comparison, None)
    result["status"] = "drift"
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_nonzero_writes_performed_rejected() -> None:
    comparison = build_comparison_result(build_snapshot(), build_snapshot())
    result = build_drift_result(comparison, None)
    result["writes_performed"] = 1
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_classified_change_requires_property_for_changed() -> None:
    comparison = build_comparison_result(build_snapshot(), build_snapshot())
    result = build_drift_result(comparison, None)
    result["status"] = "unexpected_drift"
    result["summary"] = {
        "expected_differences": 0,
        "unexpected_drift": 1,
        "affected_objects": 1,
        "property_drift": 0,
    }
    result["classified_changes"] = [
        {
            "change": "changed",
            "classification": "unexpected_drift",
            "matched_rule_ids": [],
            "object_identity": {"kind": "data_source", "path": []},
            "parent_identity": None,
            "reason": "unmatched_difference",
        }
    ]
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_diagnostics_schema_accepts_known_codes() -> None:
    payload = drift_diagnostics_failure(
        [
            DiagnosticError(
                code="missing_drift_policy",
                path="/policy",
                message="drift policy is required when comparison has differences",
            )
        ]
    )
    Draft202012Validator(_drift_diagnostics_schema()).validate(payload)


def test_diagnostics_schema_rejects_empty_errors() -> None:
    payload = {
        "diagnostic_schema": "governance-drift-diagnostics",
        "diagnostic_version": "1",
        "ok": False,
        "errors": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_diagnostics_schema()).validate(payload)


def test_diagnostics_schema_rejects_unknown_code() -> None:
    payload = {
        "diagnostic_schema": "governance-drift-diagnostics",
        "diagnostic_version": "1",
        "ok": False,
        "errors": [{"code": "unknown_code", "path": "/", "message": "bad"}],
    }
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_diagnostics_schema()).validate(payload)


def test_diagnostics_schema_rejects_empty_path() -> None:
    payload = {
        "diagnostic_schema": "governance-drift-diagnostics",
        "diagnostic_version": "1",
        "ok": False,
        "errors": [
            {"code": "policy_read_error", "path": "", "message": "unable to read drift policy"}
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_diagnostics_schema()).validate(payload)

    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_diagnostics_schema()).validate(payload)


def _description_policy_path(tmp_path) -> str:
    policy_path = tmp_path / "policy.yaml"
    write_policy(
        policy_path,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: allow-description
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
  - id: allow-table-description
    match:
      change: changed
      object:
        kind: table
        path: ["sales", "orders"]
      property: /description
""",
    )
    return str(policy_path)


def _expected_difference_result(tmp_path):
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
    )
    policy = load_drift_policy(_description_policy_path(tmp_path))
    return build_drift_result(comparison, policy)


def test_result_schema_rejects_unknown_identity_kind(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["classified_changes"][0]["object_identity"]["kind"] = "banana"
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_wrong_path_arity() -> None:
    schema = _drift_result_schema()["$defs"]["comparisonObjectIdentity"]
    validator = Draft202012Validator(schema)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"kind": "column", "path": []})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"kind": "data_source", "path": ["x"]})


def test_result_schema_rejects_invalid_parent_kind(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["classified_changes"][0]["parent_identity"] = {"kind": "database", "path": []}
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_malformed_property_pointer(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["classified_changes"][0]["property"] = "/bad~2pointer"
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)
    result["classified_changes"][0]["property"] = "/bad~"
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_accepts_valid_property_pointers(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["classified_changes"][0]["property"] = "/description"
    Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_expected_with_expectation_mismatch_reason(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["classified_changes"][0]["reason"] = "expectation_mismatch"
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_unexpected_with_matched_policy_rule_reason(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["status"] = "unexpected_drift"
    result["summary"] = {
        "expected_differences": 0,
        "unexpected_drift": 1,
        "affected_objects": 1,
        "property_drift": 1,
    }
    result["classified_changes"][0]["classification"] = "unexpected_drift"
    result["classified_changes"][0]["reason"] = "matched_policy_rule"
    result["classified_changes"][0]["matched_rule_ids"] = ["allow-description"]
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_unmatched_difference_with_rule_ids(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["status"] = "unexpected_drift"
    result["summary"] = {
        "expected_differences": 0,
        "unexpected_drift": 1,
        "affected_objects": 1,
        "property_drift": 1,
    }
    result["classified_changes"][0]["classification"] = "unexpected_drift"
    result["classified_changes"][0]["reason"] = "unmatched_difference"
    result["classified_changes"][0]["matched_rule_ids"] = ["x"]
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_expectation_mismatch_with_empty_rule_ids(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["status"] = "unexpected_drift"
    result["summary"] = {
        "expected_differences": 0,
        "unexpected_drift": 1,
        "affected_objects": 1,
        "property_drift": 1,
    }
    result["classified_changes"][0]["classification"] = "unexpected_drift"
    result["classified_changes"][0]["reason"] = "expectation_mismatch"
    result["classified_changes"][0]["matched_rule_ids"] = []
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_no_difference_with_classified_entry() -> None:
    comparison = build_comparison_result(build_snapshot(), build_snapshot())
    result = build_drift_result(comparison, None)
    result["classified_changes"] = [
        {
            "change": "changed",
            "object_identity": {"kind": "data_source", "path": []},
            "parent_identity": None,
            "property": "/description",
            "baseline": {"has_value": True, "value": None},
            "candidate": {"has_value": True, "value": "x"},
            "classification": "unexpected_drift",
            "matched_rule_ids": [],
            "reason": "unmatched_difference",
        }
    ]
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_expected_difference_with_unexpected_count(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["summary"]["unexpected_drift"] = 1
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_unexpected_drift_with_zero_unexpected_count(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["status"] = "unexpected_drift"
    result["summary"]["expected_differences"] = 0
    result["summary"]["unexpected_drift"] = 0
    result["classified_changes"][0]["classification"] = "unexpected_drift"
    result["classified_changes"][0]["reason"] = "unmatched_difference"
    result["classified_changes"][0]["matched_rule_ids"] = []
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_null_policy_on_unexpected_drift(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["status"] = "unexpected_drift"
    result["summary"] = {
        "expected_differences": 0,
        "unexpected_drift": 1,
        "affected_objects": 1,
        "property_drift": 1,
    }
    result["classified_changes"][0]["classification"] = "unexpected_drift"
    result["classified_changes"][0]["reason"] = "unmatched_difference"
    result["classified_changes"][0]["matched_rule_ids"] = []
    result["drift_policy_identity"] = None
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_expected_difference_with_property_drift(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["summary"]["property_drift"] = 1
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_expected_difference_with_zero_affected_objects(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["summary"]["affected_objects"] = 0
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_unexpected_drift_with_zero_affected_objects(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["status"] = "unexpected_drift"
    result["summary"] = {
        "expected_differences": 0,
        "unexpected_drift": 1,
        "affected_objects": 0,
        "property_drift": 1,
    }
    result["classified_changes"][0]["classification"] = "unexpected_drift"
    result["classified_changes"][0]["reason"] = "unmatched_difference"
    result["classified_changes"][0]["matched_rule_ids"] = []
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_added_data_source_classified_change(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["classified_changes"].append(
        {
            "change": "added",
            "classification": "expected_difference",
            "matched_rule_ids": ["x"],
            "object_identity": {"kind": "data_source", "path": []},
            "parent_identity": None,
            "reason": "matched_policy_rule",
        }
    )
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)


def test_result_schema_rejects_removed_database_classified_change(tmp_path) -> None:
    result = _expected_difference_result(tmp_path)
    result["classified_changes"].append(
        {
            "change": "removed",
            "classification": "expected_difference",
            "matched_rule_ids": ["x"],
            "object_identity": {"kind": "database", "path": []},
            "parent_identity": {"kind": "data_source", "path": []},
            "reason": "matched_policy_rule",
        }
    )
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_drift_result_schema()).validate(result)
