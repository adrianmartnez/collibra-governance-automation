"""JSON Schema contract tests for reconciliation and explain artifacts."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from governance.domain.authority import NormalizedAuthorityPolicySet
from governance.domain.graph import (
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.observations import (
    PropertyObservation,
    PropertyObservationSet,
    PropertyPath,
)
from governance.plans.errors import PlanSchemaError
from governance.plans.schema import validate_plan_structure
from governance.reconciliation.explain import build_explain_result
from governance.reconciliation.sources import ReconciliationSourceBundle

NS = "governance-demo"
PATH_DESC = PropertyPath(("description",))


def _load_schema(package: str, name: str) -> dict:
    text = files(package).joinpath(name).read_text(encoding="utf-8")
    return json.loads(text)


def _validator(package: str, name: str) -> Draft202012Validator:
    return Draft202012Validator(_load_schema(package, name))


def _sample_decision(*, include_winning_rule: bool = False) -> dict:
    decision: dict = {
        "state": "SINGLE_OBSERVATION",
        "reason": "SINGLE_OBSERVATION",
        "value_groups": [
            {
                "value": "customers table",
                "provenance": [
                    {
                        "provider_type": "odcs",
                        "source_ref": "c1",
                        "source_version": "1.0",
                        "observation_mode": "declared",
                    }
                ],
            }
        ],
        "effective_value": "customers table",
    }
    if include_winning_rule:
        decision["winning_rule_key"] = {
            "authority": {"provider_type": "odcs"},
            "select": {"kind": "table", "property": "/description"},
        }
    return decision


def _valid_v2_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    from test_reconciliation_plan_apply import _generate_plan

    _config, plan_path, code = _generate_plan(monkeypatch, tmp_path)
    assert code == 0
    document = json.loads(plan_path.read_text(encoding="utf-8"))
    document["reconciliation_assumptions"]["actions"][0]["properties"][0]["decision"] = (
        _sample_decision()
    )
    return document


def _mutate(document: dict, path: list[str | int], value: object) -> dict:
    copy = json.loads(json.dumps(document))
    target = copy
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return copy


def _explain_result() -> dict:
    identity = GraphNodeIdentity(
        NS,
        NODE_KIND_TABLE,
        "customers",
        parent=GraphNodeIdentity(
            NS,
            NODE_KIND_DATASET,
            "commerce",
            parent=GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, "governance_demo"),
        ),
    )
    observation = PropertyObservation(
        object_identity=identity,
        property_path=PATH_DESC,
        value="only",
        provenance=(
            ProvenanceRecord(
                provider_type="odcs",
                source_ref="c1",
                source_version="1.0",
                observation_mode="declared",
            ),
        ),
    )
    bundle = ReconciliationSourceBundle(
        observations=PropertyObservationSet.from_observations((observation,)),
        known_objects=(identity,),
    )
    return build_explain_result(
        namespace=NS,
        identity=identity,
        bundle=bundle,
        authority=NormalizedAuthorityPolicySet(),
    )


def test_v2_plan_roundtrip_validates_against_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _valid_v2_plan(tmp_path, monkeypatch)
    validator = _validator("governance.plans.schemas", "governance-plan.v2.schema.json")
    validator.validate(document)
    validate_plan_structure(document)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (
            ["reconciliation_assumptions", "actions", 0, "properties", 0, "decision", "state"],
            "BAD",
        ),
        (
            ["reconciliation_assumptions", "actions", 0, "properties", 0, "decision", "reason"],
            "BAD",
        ),
        (
            [
                "reconciliation_assumptions",
                "actions",
                0,
                "properties",
                0,
                "decision",
                "value_groups",
                0,
                "extra",
            ],
            "x",
        ),
        (
            [
                "reconciliation_assumptions",
                "actions",
                0,
                "properties",
                0,
                "decision",
                "value_groups",
                0,
                "provenance",
                0,
            ],
            {"provider_type": "odcs", "source_version": None, "observation_mode": "declared"},
        ),
        (
            [
                "reconciliation_assumptions",
                "actions",
                0,
                "properties",
                0,
                "decision",
                "value_groups",
                0,
                "provenance",
                0,
                "observation_mode",
            ],
            "bad-mode",
        ),
        (
            [
                "reconciliation_assumptions",
                "actions",
                0,
                "properties",
                0,
                "decision",
                "winning_rule_key",
                "extra",
            ],
            "x",
        ),
        (
            [
                "reconciliation_assumptions",
                "actions",
                0,
                "properties",
                0,
                "decision",
                "winning_rule_key",
                "select",
                "extra",
            ],
            "x",
        ),
        (
            [
                "reconciliation_assumptions",
                "actions",
                0,
                "properties",
                0,
                "decision",
                "winning_rule_key",
                "authority",
                "extra",
            ],
            "x",
        ),
        (
            ["reconciliation_assumptions", "actions", 0, "properties", 0, "object", "extra"],
            "x",
        ),
        (
            ["reconciliation_assumptions", "actions", 0, "properties", 0, "object", "kind"],
            "not_a_kind",
        ),
        (["reconciliation_assumptions", "actions", 0, "properties", 0, "extra"], "x"),
        (
            ["reconciliation_assumptions", "actions", 0, "properties", 0, "roles"],
            ["mutation", "mutation"],
        ),
        (
            ["reconciliation_assumptions", "actions", 0, "properties", 0, "decision"],
            "missing",
        ),
    ],
)
def test_v2_schema_rejects_invalid_assumptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: list[str | int],
    value: object,
) -> None:
    base = _valid_v2_plan(tmp_path, monkeypatch)
    if path[:6] == [
        "reconciliation_assumptions",
        "actions",
        0,
        "properties",
        0,
        "decision",
    ] and path[6:7] == ["winning_rule_key"]:
        base["reconciliation_assumptions"]["actions"][0]["properties"][0]["decision"] = (
            _sample_decision(include_winning_rule=True)
        )
    document = _mutate(base, path, value)
    with pytest.raises(PlanSchemaError):
        validate_plan_structure(document)


def test_explain_result_validates_against_schema() -> None:
    result = _explain_result()
    validator = _validator(
        "governance.reconciliation.schemas",
        "governance-explain-result.v1.schema.json",
    )
    validator.validate(result)


def test_explain_schema_rejects_unknown_root_property() -> None:
    result = _explain_result()
    result["unexpected"] = True
    validator = _validator(
        "governance.reconciliation.schemas",
        "governance-explain-result.v1.schema.json",
    )
    with pytest.raises(ValidationError):
        validator.validate(result)


def test_explain_schema_rejects_effective_value_without_flag() -> None:
    result = _explain_result()
    result["properties"][0]["has_effective_value"] = False
    result["properties"][0]["effective_value"] = "x"
    validator = _validator(
        "governance.reconciliation.schemas",
        "governance-explain-result.v1.schema.json",
    )
    with pytest.raises(ValidationError):
        validator.validate(result)


def test_explain_schema_accepts_null_effective_value_when_present() -> None:
    result = _explain_result()
    result["properties"] = [
        {
            "property": "/description",
            "state": "SINGLE_OBSERVATION",
            "reason": "SINGLE_OBSERVATION",
            "reconciliation_applicable": True,
            "reconciliation_safe": True,
            "reconciliation_reason": "safe",
            "has_effective_value": True,
            "effective_value": None,
            "value_groups": [
                {
                    "value": None,
                    "provenance": [
                        {
                            "provider_type": "odcs",
                            "source_ref": "c1",
                            "source_version": "1.0",
                            "observation_mode": "declared",
                        }
                    ],
                }
            ],
        }
    ]
    validator = _validator(
        "governance.reconciliation.schemas",
        "governance-explain-result.v1.schema.json",
    )
    validator.validate(result)


def test_reconciliation_diagnostics_schema_rejects_invalid_code() -> None:
    payload = {
        "diagnostic_schema": "governance-reconciliation-diagnostics",
        "diagnostic_version": "1",
        "ok": False,
        "errors": [{"code": "not_a_code", "path": "/x", "message": "bad"}],
    }
    validator = _validator(
        "governance.reconciliation.schemas",
        "governance-reconciliation-diagnostics.v1.schema.json",
    )
    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_explain_diagnostics_schema_rejects_invalid_code() -> None:
    payload = {
        "diagnostic_schema": "governance-explain-diagnostics",
        "diagnostic_version": "1",
        "ok": False,
        "errors": [{"code": "not_a_code", "path": "/x", "message": "bad"}],
    }
    validator = _validator(
        "governance.reconciliation.schemas",
        "governance-explain-diagnostics.v1.schema.json",
    )
    with pytest.raises(ValidationError):
        validator.validate(payload)
