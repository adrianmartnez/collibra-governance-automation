"""Classify comparison differences against drift policy."""

from __future__ import annotations

from typing import Any

from governance.comparison.compare import comparable_values_equal
from governance.drift.models import (
    CHANGE_RANK,
    CLASSIFICATION_RANK,
    PRESENCE_ONLY,
    NormalizedDriftPolicy,
    NormalizedDriftRule,
)
from governance.drift.policy import build_policy_index, expectation_projection_for_rules
from governance.identity.canonicalize import canonical_json_bytes


def classify_drift(
    comparison: dict[str, Any],
    policy: NormalizedDriftPolicy,
) -> list[dict[str, Any]]:
    index = build_policy_index(policy)
    classified: list[dict[str, Any]] = []
    for object_change in comparison["object_changes"]:
        change = object_change["change"]
        if change in {"added", "removed"}:
            classified.append(_classify_object_change(object_change, index))
        else:
            for property_change in object_change["property_changes"]:
                classified.append(_classify_property_change(object_change, property_change, index))
    classified.sort(key=_classified_sort_key)
    return classified


def _selector_key_from_change(
    change: str,
    object_identity: dict[str, Any],
    property_pointer: str | None,
) -> tuple[Any, ...]:
    identity_bytes = canonical_json_bytes(object_identity)
    return (change, identity_bytes, property_pointer)


def _classify_object_change(
    object_change: dict[str, Any],
    index: dict[tuple[Any, ...], tuple[NormalizedDriftRule, ...]],
) -> dict[str, Any]:
    change = object_change["change"]
    object_identity = object_change["object_identity"]
    parent_identity = object_change["parent_identity"]
    selector_key = _selector_key_from_change(change, object_identity, None)
    rules = index.get(selector_key)
    if not rules:
        return {
            "change": change,
            "classification": "unexpected_drift",
            "matched_rule_ids": [],
            "object_identity": object_identity,
            "parent_identity": parent_identity,
            "reason": "unmatched_difference",
        }
    projection = expectation_projection_for_rules(rules)
    assert projection == PRESENCE_ONLY
    return {
        "change": change,
        "classification": "expected_difference",
        "matched_rule_ids": sorted(rule.id for rule in rules),
        "object_identity": object_identity,
        "parent_identity": parent_identity,
        "reason": "matched_policy_rule",
    }


def _classify_property_change(
    object_change: dict[str, Any],
    property_change: dict[str, Any],
    index: dict[tuple[Any, ...], tuple[NormalizedDriftRule, ...]],
) -> dict[str, Any]:
    object_identity = object_change["object_identity"]
    parent_identity = object_change["parent_identity"]
    pointer = property_change["property"]
    baseline = property_change["baseline"]
    candidate = property_change["candidate"]
    selector_key = _selector_key_from_change("changed", object_identity, pointer)
    rules = index.get(selector_key)
    base_entry = {
        "baseline": baseline,
        "candidate": candidate,
        "change": "changed",
        "object_identity": object_identity,
        "parent_identity": parent_identity,
        "property": pointer,
    }
    if not rules:
        return {
            **base_entry,
            "classification": "unexpected_drift",
            "matched_rule_ids": [],
            "reason": "unmatched_difference",
        }
    projection = expectation_projection_for_rules(rules)
    rule_ids = sorted(rule.id for rule in rules)
    if projection == PRESENCE_ONLY:
        return {
            **base_entry,
            "classification": "expected_difference",
            "matched_rule_ids": rule_ids,
            "reason": "matched_policy_rule",
        }
    if _expectations_satisfied(rules[0].expected, baseline, candidate):
        return {
            **base_entry,
            "classification": "expected_difference",
            "matched_rule_ids": rule_ids,
            "reason": "matched_policy_rule",
        }
    return {
        **base_entry,
        "classification": "unexpected_drift",
        "matched_rule_ids": rule_ids,
        "reason": "expectation_mismatch",
    }


def _expectations_satisfied(
    expected: dict[str, Any] | None,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    if expected is None:
        return True
    if "baseline" in expected and not _comparable_side_matches(expected["baseline"], baseline):
        return False
    if "candidate" in expected:
        return _comparable_side_matches(expected["candidate"], candidate)
    return True


def _comparable_side_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected_has = bool(expected.get("has_value"))
    actual_has = bool(actual.get("has_value"))
    if expected_has != actual_has:
        return False
    if not expected_has:
        return True
    return comparable_values_equal(expected["value"], actual["value"])


def _classified_sort_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    identity_bytes = canonical_json_bytes(entry["object_identity"])
    change = entry["change"]
    prop = entry.get("property", "")
    return (
        CLASSIFICATION_RANK[entry["classification"]],
        identity_bytes,
        CHANGE_RANK[change],
        prop,
        entry["reason"],
    )


def derive_status(classified: list[dict[str, Any]]) -> str:
    if not classified:
        return "no_difference"
    if any(item["classification"] == "unexpected_drift" for item in classified):
        return "unexpected_drift"
    return "expected_difference"
