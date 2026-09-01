"""Drift classification and result-building tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest_comparison import build_snapshot
from conftest_drift import write_policy
from governance.comparison import build_comparison_result
from governance.domain import Column, make_column_id
from governance.drift.classify import classify_drift, derive_status
from governance.drift.errors import CODE_MISSING_DRIFT_POLICY, DriftError
from governance.drift.load import load_drift_policy
from governance.drift.result import build_drift_result


def _description_policy_yaml(*extra_rules: str) -> str:
    extra = "\n".join(extra_rules)
    return f"""drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: allow-description
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
{extra}
"""


def test_empty_policy_marks_all_unexpected_without_raising(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_policy(
        policy_path,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
""",
    )
    policy = load_drift_policy(policy_path)
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
    )
    result = build_drift_result(comparison, policy)
    assert result["status"] == "unexpected_drift"
    assert result["summary"]["unexpected_drift"] > 0
    assert result["summary"]["expected_differences"] == 0
    assert all(
        item["classification"] == "unexpected_drift" for item in result["classified_changes"]
    )


def test_missing_policy_raises_when_comparison_differs() -> None:
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
    )
    with pytest.raises(DriftError) as exc:
        build_drift_result(comparison, None)
    assert exc.value.errors[0].code == CODE_MISSING_DRIFT_POLICY


def test_identical_comparison_without_policy_is_no_difference() -> None:
    comparison = build_comparison_result(build_snapshot(), build_snapshot())
    result = build_drift_result(comparison, None)
    assert result["status"] == "no_difference"
    assert result["drift_policy_identity"] is None
    assert result["classified_changes"] == []


def test_expected_difference_when_policy_matches(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_policy(policy_path, _description_policy_yaml())
    policy = load_drift_policy(policy_path)
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
    )
    result = build_drift_result(comparison, policy)
    matched = [
        item
        for item in result["classified_changes"]
        if item["object_identity"]["kind"] == "data_source"
        and item.get("property") == "/description"
    ]
    assert matched
    assert matched[0]["classification"] == "expected_difference"
    assert matched[0]["matched_rule_ids"] == ["allow-description"]
    assert matched[0]["reason"] == "matched_policy_rule"
    assert result["summary"]["expected_differences"] >= 1


def test_expectation_mismatch_is_unexpected_drift(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_policy(
        policy_path,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: strict-description
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
    expected:
      baseline:
        has_value: true
        value: "old"
      candidate:
        has_value: true
        value: "updated"
""",
    )
    policy = load_drift_policy(policy_path)
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
    )
    result = build_drift_result(comparison, policy)
    data_source_entries = [
        item
        for item in result["classified_changes"]
        if item["object_identity"]["kind"] == "data_source"
        and item.get("property") == "/description"
    ]
    assert data_source_entries
    assert data_source_entries[0]["classification"] == "unexpected_drift"
    assert data_source_entries[0]["reason"] == "expectation_mismatch"


def test_direction_added_rule_does_not_match_removed() -> None:
    extra = Column(
        id=make_column_id("governance-demo", "governance_demo", "sales", "orders", "extra"),
        name="extra",
        data_type="text",
        ordinal_position=2,
        nullable=True,
    )
    comparison = build_comparison_result(build_snapshot(), build_snapshot(extra_column=extra))
    from governance.drift.policy import parse_and_normalize_policy

    policy = parse_and_normalize_policy(
        {
            "drift_schema": "governance-drift-policy",
            "drift_version": "1",
            "rules": [
                {
                    "id": "allow-added-column",
                    "match": {
                        "change": "added",
                        "object": {
                            "kind": "column",
                            "path": ["sales", "orders", "extra"],
                        },
                    },
                }
            ],
        }
    )
    classified = classify_drift(comparison, policy)
    added = [item for item in classified if item["change"] == "added"]
    assert added
    assert added[0]["classification"] == "expected_difference"

    reverse = build_comparison_result(build_snapshot(extra_column=extra), build_snapshot())
    reverse_classified = classify_drift(reverse, policy)
    removed = [item for item in reverse_classified if item["change"] == "removed"]
    assert removed
    assert removed[0]["classification"] == "unexpected_drift"


def test_derive_status_helpers() -> None:
    assert derive_status([]) == "no_difference"
    assert derive_status([{"classification": "expected_difference"}]) == "expected_difference"
    assert derive_status([{"classification": "unexpected_drift"}]) == "unexpected_drift"
