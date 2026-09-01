"""Deterministic directional comparison of projected snapshots."""

from __future__ import annotations

from typing import Any

from governance.comparison.projection import (
    ComparableObject,
    ComparablePropertyValue,
    ComparisonObjectIdentity,
    ProjectedSnapshot,
)
from governance.identity.json_values import canonical_value_fingerprint


def comparable_values_equal(left: Any, right: Any) -> bool:
    return canonical_value_fingerprint(left) == canonical_value_fingerprint(right)


def _property_values_equal(left: ComparablePropertyValue, right: ComparablePropertyValue) -> bool:
    if left.has_value != right.has_value:
        return False
    if not left.has_value:
        return True
    return comparable_values_equal(left.value, right.value)


def _diff_properties(
    baseline: ComparableObject,
    candidate: ComparableObject,
) -> list[dict[str, Any]]:
    keys = sorted(set(baseline.properties) | set(candidate.properties))
    changes: list[dict[str, Any]] = []
    for key in keys:
        left = baseline.properties.get(key)
        right = candidate.properties.get(key)
        if left is None:
            left = ComparablePropertyValue(has_value=False)
        if right is None:
            right = ComparablePropertyValue(has_value=False)
        if _property_values_equal(left, right):
            continue
        changes.append(
            {
                "baseline": left.to_dict(),
                "candidate": right.to_dict(),
                "property": key,
            }
        )
    return changes


CHANGE_RANK = {"added": 0, "removed": 1, "changed": 2}


def compare_projected(
    baseline: ProjectedSnapshot,
    candidate: ProjectedSnapshot,
) -> dict[str, Any]:
    """Compare two projected sides; return summary + object_changes (no envelope)."""
    base_ids = set(baseline.objects)
    cand_ids = set(candidate.objects)

    added_ids = sorted(cand_ids - base_ids)
    removed_ids = sorted(base_ids - cand_ids)
    matched_ids = sorted(base_ids & cand_ids)

    object_changes: list[dict[str, Any]] = []
    changed_count = 0
    property_change_count = 0
    unchanged_count = 0

    for identity in added_ids:
        obj = candidate.objects[identity]
        object_changes.append(
            {
                "change": "added",
                "object_identity": identity.to_dict(),
                "parent_identity": (
                    None if obj.parent_identity is None else obj.parent_identity.to_dict()
                ),
                "property_changes": [],
            }
        )

    for identity in removed_ids:
        obj = baseline.objects[identity]
        object_changes.append(
            {
                "change": "removed",
                "object_identity": identity.to_dict(),
                "parent_identity": (
                    None if obj.parent_identity is None else obj.parent_identity.to_dict()
                ),
                "property_changes": [],
            }
        )

    for identity in matched_ids:
        left = baseline.objects[identity]
        right = candidate.objects[identity]
        prop_changes = _diff_properties(left, right)
        if not prop_changes:
            unchanged_count += 1
            continue
        changed_count += 1
        property_change_count += len(prop_changes)
        object_changes.append(
            {
                "change": "changed",
                "object_identity": identity.to_dict(),
                "parent_identity": (
                    None if left.parent_identity is None else left.parent_identity.to_dict()
                ),
                "property_changes": prop_changes,
            }
        )

    object_changes.sort(
        key=lambda item: (
            CHANGE_RANK[item["change"]],
            ComparisonObjectIdentity(
                kind=item["object_identity"]["kind"],
                path=tuple(item["object_identity"]["path"]),
            ).canonical_bytes(),
        )
    )

    summary = {
        "added": len(added_ids),
        "removed": len(removed_ids),
        "changed": changed_count,
        "unchanged": unchanged_count,
        "property_changes": property_change_count,
    }
    return {"object_changes": object_changes, "summary": summary}
