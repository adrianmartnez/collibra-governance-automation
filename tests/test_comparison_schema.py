"""JSON Schema validation for comparison artifacts."""

from __future__ import annotations

import json
from importlib.resources import files

import jsonschema
import pytest
from jsonschema import Draft202012Validator

from conftest_comparison import build_snapshot
from governance.comparison import build_comparison_result


def _comparison_schema() -> dict:
    text = (
        files("governance.comparison.schemas")
        .joinpath("governance-snapshot-comparison.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def _diagnostics_schema() -> dict:
    text = (
        files("governance.comparison.schemas")
        .joinpath("governance-comparison-diagnostics.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def test_valid_comparison_result_accepted() -> None:
    result = build_comparison_result(build_snapshot(), build_snapshot())
    Draft202012Validator(_comparison_schema()).validate(result)


def test_extra_root_rejected() -> None:
    result = build_comparison_result(build_snapshot(), build_snapshot())
    result["extra"] = True
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_comparison_schema()).validate(result)


def test_invalid_status_rejected() -> None:
    result = build_comparison_result(build_snapshot(), build_snapshot())
    result["status"] = "drift"
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_comparison_schema()).validate(result)


def test_identity_arity_rejected() -> None:
    schema = _comparison_schema()
    identity_schema = schema["$defs"]["comparisonObjectIdentity"]
    validator = Draft202012Validator(identity_schema)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"kind": "column", "path": []})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"kind": "data_source", "path": ["x"]})


def test_comparable_property_value_cases() -> None:
    schema = _comparison_schema()["$defs"]["comparablePropertyValue"]
    validator = Draft202012Validator(schema)
    validator.validate({"has_value": True, "value": None})
    validator.validate({"has_value": False})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"has_value": False, "value": None})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"has_value": True})


def test_added_with_property_changes_rejected() -> None:
    result = build_comparison_result(build_snapshot(), build_snapshot())
    result["status"] = "different"
    result["summary"] = {
        "added": 1,
        "removed": 0,
        "changed": 0,
        "unchanged": 0,
        "property_changes": 0,
    }
    result["object_changes"] = [
        {
            "change": "added",
            "object_identity": {"kind": "schema", "path": ["sales"]},
            "parent_identity": {"kind": "database", "path": []},
            "property_changes": [
                {
                    "property": "/name",
                    "baseline": {"has_value": False},
                    "candidate": {"has_value": True, "value": "x"},
                }
            ],
        }
    ]
    # content_identity may be stale; schema still rejects shape
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_comparison_schema()).validate(result)


def test_diagnostics_schema_accepts_codes() -> None:
    payload = {
        "diagnostic_schema": "governance-comparison-diagnostics",
        "diagnostic_version": "1",
        "ok": False,
        "errors": [
            {
                "code": "root_alignment_required",
                "path": "/root_alignment/source",
                "message": "required",
            }
        ],
    }
    Draft202012Validator(_diagnostics_schema()).validate(payload)


def test_diagnostics_schema_rejects_empty_errors() -> None:
    payload = {
        "diagnostic_schema": "governance-comparison-diagnostics",
        "diagnostic_version": "1",
        "ok": False,
        "errors": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_diagnostics_schema()).validate(payload)


def test_diagnostics_schema_rejects_empty_path() -> None:
    payload = {
        "diagnostic_schema": "governance-comparison-diagnostics",
        "diagnostic_version": "1",
        "ok": False,
        "errors": [{"code": "read_error", "path": "", "message": "unable to read snapshot"}],
    }
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(_diagnostics_schema()).validate(payload)


def test_parent_kind_contract_rejects_invalid_parent() -> None:
    schema = _comparison_schema()
    validator = Draft202012Validator(schema)
    base = build_comparison_result(build_snapshot(), build_snapshot())
    base["status"] = "different"
    base["summary"] = {
        "added": 1,
        "removed": 0,
        "changed": 0,
        "unchanged": 0,
        "property_changes": 0,
    }
    invalid_cases = [
        {
            "change": "added",
            "object_identity": {"kind": "data_source", "path": []},
            "parent_identity": {"kind": "database", "path": []},
            "property_changes": [],
        },
        {
            "change": "added",
            "object_identity": {"kind": "database", "path": []},
            "parent_identity": {"kind": "schema", "path": ["sales"]},
            "property_changes": [],
        },
        {
            "change": "added",
            "object_identity": {"kind": "schema", "path": ["sales"]},
            "parent_identity": {"kind": "table", "path": ["sales", "orders"]},
            "property_changes": [],
        },
        {
            "change": "added",
            "object_identity": {"kind": "column", "path": ["sales", "orders", "id"]},
            "parent_identity": {"kind": "database", "path": []},
            "property_changes": [],
        },
        {
            "change": "added",
            "object_identity": {"kind": "primary_key", "path": ["sales", "orders", "pk"]},
            "parent_identity": {"kind": "schema", "path": ["sales"]},
            "property_changes": [],
        },
        {
            "change": "added",
            "object_identity": {"kind": "relationship", "path": ["sales", "orders", "rel"]},
            "parent_identity": {"kind": "column", "path": ["sales", "orders", "id"]},
            "property_changes": [],
        },
    ]
    for change in invalid_cases:
        payload = dict(base)
        payload["object_changes"] = [change]
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(payload)
