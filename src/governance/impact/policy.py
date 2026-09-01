"""Policy relevance matching for impact analysis (not evaluation).

Deliberately replicates the current minimal ``PolicySelector`` contract used by
``governance.policy.evaluate`` (exact kind, exact object_id, id_prefix startswith)
without importing private evaluator helpers or evaluating rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from governance.domain.graph import GraphNodeIdentity
from governance.domain.physical_projection import project_physical_identity
from governance.policy.models import NormalizedPolicySet, PolicySelector


@dataclass(frozen=True, slots=True)
class ProjectedObject:
    """Legacy selector projection of a physical graph node."""

    object_kind: str
    object_id: str
    node: GraphNodeIdentity

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node.to_dict(),
            "object_id": self.object_id,
            "object_kind": self.object_kind,
        }


@dataclass(frozen=True, slots=True)
class AffectedPolicy:
    """Configured policy relevant to at least one projected impact node."""

    policy_id: str
    severity: str
    rule_type: str
    matched_objects: tuple[ProjectedObject, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_objects": [item.to_dict() for item in self.matched_objects],
            "policy_id": self.policy_id,
            "rule_type": self.rule_type,
            "severity": self.severity,
        }


def project_physical_selector_target(
    identity: GraphNodeIdentity,
) -> ProjectedObject | None:
    """Project a graph identity to a legacy policy selector target when safe.

    Only physical dbt/OpenLineage-style chains are projectable:

    - data_source (no parent) → database
    - dataset → data_source → schema
    - table → dataset → data_source → table
    - column → table → dataset → data_source → column

    Generic ODCS datasets/columns, contracts, transformations, and incomplete
    chains return ``None`` (no coercion).
    """
    projected = project_physical_identity(identity)
    if projected is None:
        return None
    return ProjectedObject(
        object_kind=projected.object_kind,
        object_id=projected.local_id,
        node=projected.node,
    )


def selector_matches(selector: PolicySelector, projected: ProjectedObject) -> bool:
    """Return whether ``selector`` matches ``projected`` under current PolicySelector rules."""
    if selector.kind != projected.object_kind:
        return False
    if selector.object_id is not None and projected.object_id != selector.object_id:
        return False
    return selector.id_prefix is None or projected.object_id.startswith(selector.id_prefix)


def _matched_sort_key(item: ProjectedObject) -> tuple[str, str, bytes]:
    return (item.object_kind, item.object_id, item.node.canonical_bytes())


def match_affected_policies(
    policy_set: NormalizedPolicySet,
    policy_relevant_nodes: tuple[GraphNodeIdentity, ...] | list[GraphNodeIdentity],
) -> tuple[AffectedPolicy, ...]:
    """Identify configured policies relevant to projected policy-relevant nodes."""
    projections: list[ProjectedObject] = []
    seen: set[tuple[str, str, GraphNodeIdentity]] = set()
    for node in policy_relevant_nodes:
        projected = project_physical_selector_target(node)
        if projected is None:
            continue
        key = (projected.object_kind, projected.object_id, projected.node)
        if key in seen:
            continue
        seen.add(key)
        projections.append(projected)

    affected: list[AffectedPolicy] = []
    for policy in policy_set.policies:
        matched = [
            projected for projected in projections if selector_matches(policy.select, projected)
        ]
        if not matched:
            continue
        unique: dict[tuple[str, str, GraphNodeIdentity], ProjectedObject] = {}
        for item in matched:
            unique[(item.object_kind, item.object_id, item.node)] = item
        ordered = tuple(sorted(unique.values(), key=_matched_sort_key))
        affected.append(
            AffectedPolicy(
                policy_id=policy.id,
                severity=policy.severity,
                rule_type=policy.rule_type,
                matched_objects=ordered,
            )
        )
    return tuple(sorted(affected, key=lambda item: item.policy_id))


def affected_policies_to_dicts(
    affected: tuple[AffectedPolicy, ...] | list[AffectedPolicy],
) -> list[dict[str, Any]]:
    return [item.to_dict() for item in affected]
