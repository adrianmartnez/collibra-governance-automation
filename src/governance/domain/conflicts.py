"""Property conflict analysis against authority rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from governance.domain.authority import AuthorityRuleKey, NormalizedAuthorityPolicySet
from governance.domain.graph import GraphNodeIdentity
from governance.domain.observations import (
    PropertyObservation,
    PropertyObservationSet,
    PropertyPath,
)
from governance.identity.hashing import ContentIdentity, property_conflict_report_identity

PROPERTY_CONFLICT_REPORT_SCHEMA = "governance-property-conflicts"
PROPERTY_CONFLICT_REPORT_VERSION = "1"

ConflictState = Literal[
    "SINGLE_OBSERVATION",
    "AGREEMENT",
    "RESOLVED_BY_AUTHORITY",
    "UNRESOLVED_CONFLICT",
    "INVALID_OR_AMBIGUOUS_AUTHORITY",
]

ConflictReason = Literal[
    "SINGLE_OBSERVATION",
    "AGREEMENT",
    "RESOLVED_BY_AUTHORITY",
    "NO_AUTHORITY_RULE",
    "AUTHORITATIVE_SOURCE_NOT_OBSERVED",
    "AUTHORITATIVE_SOURCE_CONFLICTED",
    "INVALID_OR_AMBIGUOUS_AUTHORITY",
]


@dataclass(frozen=True, slots=True)
class PropertyConflictResult:
    """Conflict analysis result for one object+property path."""

    object_identity: GraphNodeIdentity
    property_path: PropertyPath
    state: ConflictState
    reason: ConflictReason
    value_groups: tuple[PropertyObservation, ...]
    effective_value: Any = None
    has_effective_value: bool = False
    winning_rule_key: AuthorityRuleKey | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.object_identity, GraphNodeIdentity):
            raise TypeError("object_identity must be GraphNodeIdentity")
        if not isinstance(self.property_path, PropertyPath):
            raise TypeError("property_path must be PropertyPath")
        ordered = tuple(
            sorted(
                self.value_groups,
                key=lambda item: item.value_fingerprint(),
            )
        )
        object.__setattr__(self, "value_groups", ordered)

    def to_identity_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "object": self.object_identity.to_dict(),
            "property": self.property_path.to_pointer(),
            "reason": self.reason,
            "state": self.state,
            "value_groups": [group.to_value_group_dict() for group in self.value_groups],
        }
        if self.has_effective_value:
            payload["effective_value"] = self.effective_value
        if self.winning_rule_key is not None:
            payload["winning_rule_key"] = self.winning_rule_key.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class PropertyConflictReport:
    """Aggregate conflict analysis report."""

    results: tuple[PropertyConflictResult, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "results",
            tuple(
                sorted(
                    self.results,
                    key=lambda item: (
                        item.object_identity.canonical_bytes(),
                        item.property_path.to_pointer().encode("utf-8"),
                    ),
                )
            ),
        )

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            "conflict_schema": PROPERTY_CONFLICT_REPORT_SCHEMA,
            "conflict_version": PROPERTY_CONFLICT_REPORT_VERSION,
            "results": [result.to_identity_dict() for result in self.results],
        }

    def content_identity(self) -> ContentIdentity:
        return property_conflict_report_identity(self.to_identity_dict())


def analyze_property_conflicts(
    observations: PropertyObservationSet,
    authority: NormalizedAuthorityPolicySet,
) -> PropertyConflictReport:
    """Analyze property observations against normalized authority rules."""
    if not isinstance(observations, PropertyObservationSet):
        raise TypeError("observations must be PropertyObservationSet")
    if not isinstance(authority, NormalizedAuthorityPolicySet):
        raise TypeError("authority must be NormalizedAuthorityPolicySet")

    groups: dict[tuple[bytes, str], list[PropertyObservation]] = {}
    for observation in observations.observations:
        key = (
            observation.object_identity.canonical_bytes(),
            observation.property_path.to_pointer(),
        )
        groups.setdefault(key, []).append(observation)

    results: list[PropertyConflictResult] = []
    for group in groups.values():
        results.append(_analyze_group(tuple(group), authority))
    return PropertyConflictReport(results=tuple(results))


def _analyze_group(
    group: tuple[PropertyObservation, ...],
    authority: NormalizedAuthorityPolicySet,
) -> PropertyConflictResult:
    base = group[0]
    object_identity = base.object_identity
    property_path = base.property_path
    value_groups = tuple(
        sorted(group, key=lambda item: item.value_fingerprint()),
    )

    if len(value_groups) == 1:
        observation = value_groups[0]
        if len(observation.provenance) == 1:
            return PropertyConflictResult(
                object_identity=object_identity,
                property_path=property_path,
                state="SINGLE_OBSERVATION",
                reason="SINGLE_OBSERVATION",
                value_groups=value_groups,
                effective_value=observation.value,
                has_effective_value=True,
            )
        return PropertyConflictResult(
            object_identity=object_identity,
            property_path=property_path,
            state="AGREEMENT",
            reason="AGREEMENT",
            value_groups=value_groups,
            effective_value=observation.value,
            has_effective_value=True,
        )

    matching = authority.matching_rules(object_identity, property_path)
    if not matching:
        return PropertyConflictResult(
            object_identity=object_identity,
            property_path=property_path,
            state="UNRESOLVED_CONFLICT",
            reason="NO_AUTHORITY_RULE",
            value_groups=value_groups,
        )

    max_rank = max(rule.key.selector.specificity_rank for rule in matching)
    winners = [rule for rule in matching if rule.key.selector.specificity_rank == max_rank]
    distinct_keys: dict[AuthorityRuleKey, None] = {}
    for rule in winners:
        distinct_keys[rule.key] = None
    if len(distinct_keys) > 1:
        # Defensive: same selector / different targets should not reach here via loader,
        # but programmatic sets may. Do not pick by order.
        return PropertyConflictResult(
            object_identity=object_identity,
            property_path=property_path,
            state="INVALID_OR_AMBIGUOUS_AUTHORITY",
            reason="INVALID_OR_AMBIGUOUS_AUTHORITY",
            value_groups=value_groups,
        )

    winning_rule = winners[0]
    winning_key = winning_rule.key
    authorized_values: list[PropertyObservation] = []
    for observation in value_groups:
        if any(winning_key.authority.matches(record) for record in observation.provenance):
            authorized_values.append(observation)

    if len(authorized_values) == 0:
        return PropertyConflictResult(
            object_identity=object_identity,
            property_path=property_path,
            state="UNRESOLVED_CONFLICT",
            reason="AUTHORITATIVE_SOURCE_NOT_OBSERVED",
            value_groups=value_groups,
            winning_rule_key=winning_key,
        )
    if len(authorized_values) == 1:
        return PropertyConflictResult(
            object_identity=object_identity,
            property_path=property_path,
            state="RESOLVED_BY_AUTHORITY",
            reason="RESOLVED_BY_AUTHORITY",
            value_groups=value_groups,
            effective_value=authorized_values[0].value,
            has_effective_value=True,
            winning_rule_key=winning_key,
        )
    return PropertyConflictResult(
        object_identity=object_identity,
        property_path=property_path,
        state="UNRESOLVED_CONFLICT",
        reason="AUTHORITATIVE_SOURCE_CONFLICTED",
        value_groups=value_groups,
        winning_rule_key=winning_key,
    )
