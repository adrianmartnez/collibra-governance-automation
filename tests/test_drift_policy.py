"""Drift policy parse, validate, and normalize tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest_drift import write_policy
from governance.drift.errors import CODE_AMBIGUOUS_DRIFT_POLICY, CODE_INVALID_POLICY, DriftError
from governance.drift.load import load_drift_policy
from governance.drift.pointer import join_pointer
from governance.drift.policy import _reject_extra_keys, parse_and_normalize_policy


def _minimal_policy(*rules: str) -> str:
    rules_body = "[]" if not rules else "\n" + "\n".join(rules)
    return f"""drift_schema: governance-drift-policy
drift_version: "1"
rules: {rules_body}
"""


def test_empty_rules_array_accepted(tmp_path: Path) -> None:
    write_policy(tmp_path / "policy.yaml", _minimal_policy())
    policy = load_drift_policy(tmp_path / "policy.yaml")
    assert policy.rules == ()


def test_duplicate_yaml_keys_rejected(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
rules: []
""",
    )
    with pytest.raises(DriftError) as exc:
        load_drift_policy(tmp_path / "policy.yaml")
    assert exc.value.errors[0].code == "policy_parse_error"


def test_rule_id_stripped(tmp_path: Path) -> None:
    policy = parse_and_normalize_policy(
        {
            "drift_schema": "governance-drift-policy",
            "drift_version": "1",
            "rules": [
                {
                    "id": "  allow-description  ",
                    "match": {
                        "change": "changed",
                        "object": {"kind": "data_source", "path": []},
                        "property": "/description",
                    },
                }
            ],
        }
    )
    assert policy.rules[0].id == "allow-description"


def test_duplicate_rule_id_after_strip_rejected() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "  dup  ",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/description",
                },
            },
            {
                "id": "dup",
                "match": {
                    "change": "changed",
                    "object": {"kind": "table", "path": ["sales", "orders"]},
                    "property": "/description",
                },
            },
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert any(
        item.code == CODE_INVALID_POLICY and "duplicate" in item.message
        for item in exc.value.errors
    )


def test_rule_ids_are_case_sensitive() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "Rule-A",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/description",
                },
            },
            {
                "id": "rule-a",
                "match": {
                    "change": "changed",
                    "object": {"kind": "table", "path": ["sales", "orders"]},
                    "property": "/description",
                },
            },
        ],
    }
    policy = parse_and_normalize_policy(document)
    assert {rule.id for rule in policy.rules} == {"Rule-A", "rule-a"}


def test_impossible_object_kind_rejected() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "bad-kind",
                "match": {
                    "change": "added",
                    "object": {"kind": "view", "path": []},
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert all(item.code == CODE_INVALID_POLICY for item in exc.value.errors)


def test_property_incompatible_with_kind_rejected() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "bad-property",
                "match": {
                    "change": "changed",
                    "object": {"kind": "column", "path": ["sales", "orders", "id"]},
                    "property": "/system_type",
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert any("not compatible" in item.message for item in exc.value.errors)


def test_empty_expected_block_rejected() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "empty-expected",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/description",
                },
                "expected": {},
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert all(item.code == CODE_INVALID_POLICY for item in exc.value.errors)


def test_ambiguous_expectations_rejected_at_parse_time() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "presence-only",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/description",
                },
            },
            {
                "id": "with-expected",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/description",
                },
                "expected": {
                    "baseline": {"has_value": True, "value": None},
                    "candidate": {"has_value": True, "value": "updated"},
                },
            },
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert any(item.code == CODE_AMBIGUOUS_DRIFT_POLICY for item in exc.value.errors)


def test_changed_rule_requires_property_selector() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "missing-property",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert all(item.code == CODE_INVALID_POLICY for item in exc.value.errors)


def test_property_on_added_change_rejected() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "bad-added",
                "match": {
                    "change": "added",
                    "object": {"kind": "column", "path": ["sales", "orders", "extra"]},
                    "property": "/name",
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert all(item.code == CODE_INVALID_POLICY for item in exc.value.errors)


def test_runtime_policy_schema_validation_executed() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": " ",
                "match": {
                    "change": "added",
                    "object": {"kind": "data_source", "path": []},
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert any("schema validation" in item.message for item in exc.value.errors)


@pytest.mark.parametrize("segment", [123, True, None])
def test_non_string_identity_path_segments_rejected(segment) -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "bad-path",
                "match": {
                    "change": "added",
                    "object": {"kind": "schema", "path": [segment]},
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert all(item.code == CODE_INVALID_POLICY for item in exc.value.errors)


def test_nested_duplicate_yaml_key_rejected(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: dup-nested
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
      property: /description
""",
    )
    with pytest.raises(DriftError) as exc:
        load_drift_policy(tmp_path / "policy.yaml")
    assert exc.value.errors[0].code == "policy_parse_error"


def test_complex_yaml_mapping_key_rejected(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
? [a, b]
: ignored
""",
    )
    with pytest.raises(DriftError) as exc:
        load_drift_policy(tmp_path / "policy.yaml")
    assert exc.value.errors[0].code == "policy_parse_error"


def test_numeric_yaml_mapping_key_rejected(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
1: value
""",
    )
    with pytest.raises(DriftError) as exc:
        load_drift_policy(tmp_path / "policy.yaml")
    assert exc.value.errors[0].code == "policy_parse_error"


def test_boolean_yaml_mapping_key_rejected(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
true: value
""",
    )
    with pytest.raises(DriftError) as exc:
        load_drift_policy(tmp_path / "policy.yaml")
    assert exc.value.errors[0].code == "policy_parse_error"


def test_cyclic_yaml_alias_rejected(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: cyclic
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
    expected:
      baseline: &cycle
        has_value: true
        value: *cycle
""",
    )
    with pytest.raises(DriftError) as exc:
        load_drift_policy(tmp_path / "policy.yaml")
    assert exc.value.errors[0].code in {"invalid_policy", "policy_parse_error"}


@pytest.mark.parametrize("value", [".nan", ".inf", "-.inf"])
def test_non_finite_yaml_expected_values_rejected(tmp_path: Path, value: str) -> None:
    write_policy(
        tmp_path / "policy.yaml",
        f"""drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: bad-float
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
    expected:
      baseline:
        has_value: true
        value: {value}
""",
    )
    with pytest.raises(DriftError) as exc:
        load_drift_policy(tmp_path / "policy.yaml")
    assert any(item.code == CODE_INVALID_POLICY for item in exc.value.errors)


def test_yaml_datetime_expected_value_rejected(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: bad-date
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
    expected:
      baseline:
        has_value: true
        value: 2020-01-01
""",
    )
    with pytest.raises(DriftError) as exc:
        load_drift_policy(tmp_path / "policy.yaml")
    assert any(item.code == CODE_INVALID_POLICY for item in exc.value.errors)


def test_data_source_system_type_rule_rejected() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "bad-system-type",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/system_type",
                },
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert any("not compatible" in item.message for item in exc.value.errors)


def test_same_selector_same_expectation_different_ids_valid() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "rule-a",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/description",
                },
                "expected": {
                    "baseline": {"has_value": True, "value": None},
                    "candidate": {"has_value": True, "value": "updated"},
                },
            },
            {
                "id": "rule-b",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/description",
                },
                "expected": {
                    "baseline": {"has_value": True, "value": None},
                    "candidate": {"has_value": True, "value": "updated"},
                },
            },
        ],
    }
    policy = parse_and_normalize_policy(document)
    assert {rule.id for rule in policy.rules} == {"rule-a", "rule-b"}


def test_ambiguity_invariant_under_rule_declaration_reversal() -> None:
    base = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "presence-only",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/description",
                },
            },
            {
                "id": "with-expected",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/description",
                },
                "expected": {
                    "baseline": {"has_value": True, "value": None},
                    "candidate": {"has_value": True, "value": "updated"},
                },
            },
        ],
    }
    reversed_rules = {**base, "rules": list(reversed(base["rules"]))}
    with pytest.raises(DriftError) as first:
        parse_and_normalize_policy(base)
    with pytest.raises(DriftError) as second:
        parse_and_normalize_policy(reversed_rules)
    assert all(item.code == CODE_AMBIGUOUS_DRIFT_POLICY for item in first.value.errors)
    assert all(item.code == CODE_AMBIGUOUS_DRIFT_POLICY for item in second.value.errors)


def test_join_pointer_escapes_slash_and_tilde() -> None:
    assert join_pointer("/rules/0", "foo/bar") == "/rules/0/foo~1bar"
    assert join_pointer("/rules/0", "a~b") == "/rules/0/a~0b"


def test_reject_extra_keys_uses_escaped_pointer_path() -> None:
    with pytest.raises(DriftError) as exc:
        _reject_extra_keys({"foo/bar": True}, path="/rules/0", allowed=set())
    assert exc.value.errors[0].path == "/rules/0/foo~1bar"


def test_dynamic_unexpected_key_with_slash_escaped_path() -> None:
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "extra-key",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": "/description",
                },
                "foo/bar": True,
            }
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert all(item.code == CODE_INVALID_POLICY for item in exc.value.errors)


def test_ambiguity_message_does_not_echo_property_value() -> None:
    long_segment = "x" * 200
    long_property = f"/technical_attributes/{long_segment}"
    document = {
        "drift_schema": "governance-drift-policy",
        "drift_version": "1",
        "rules": [
            {
                "id": "presence-only",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": long_property,
                },
            },
            {
                "id": "with-expected",
                "match": {
                    "change": "changed",
                    "object": {"kind": "data_source", "path": []},
                    "property": long_property,
                },
                "expected": {
                    "baseline": {"has_value": True, "value": None},
                    "candidate": {"has_value": True, "value": "updated"},
                },
            },
        ],
    }
    with pytest.raises(DriftError) as exc:
        parse_and_normalize_policy(document)
    assert any(item.code == CODE_AMBIGUOUS_DRIFT_POLICY for item in exc.value.errors)
    assert all(long_property not in item.message for item in exc.value.errors)
    assert all("conflicting expectations" in item.message for item in exc.value.errors)
