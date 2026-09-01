"""Reconciliation safety assessment shared by planner and explain."""

from __future__ import annotations

from dataclasses import dataclass

from governance.domain.conflicts import PropertyConflictResult
from governance.domain.graph import GraphNodeIdentity
from governance.domain.observations import PropertyPath
from governance.reconciliation.targets import (
    convert_effective_value,
    path_applicable_to_identity,
    target_field_for_path,
)

REASON_SAFE = "safe"
REASON_UNRESOLVED = "unresolved_conflict"
REASON_INVALID = "invalid_or_ambiguous_authority"
REASON_UNSUPPORTED = "unsupported_effective_value"
REASON_NOT_MAPPED = "not_reconciliation_mapped"

SAFE_STATES = frozenset({"SINGLE_OBSERVATION", "AGREEMENT", "RESOLVED_BY_AUTHORITY"})
UNSAFE_STATES = frozenset({"UNRESOLVED_CONFLICT", "INVALID_OR_AMBIGUOUS_AUTHORITY"})


@dataclass(frozen=True, slots=True)
class ReconciliationAssessment:
    applicable: bool
    safe: bool
    reason: str


def conflict_decision_safe(result: PropertyConflictResult) -> bool:
    return result.state in SAFE_STATES


def assess_reconciliation(
    result: PropertyConflictResult,
    *,
    identity: GraphNodeIdentity | None = None,
    property_path: PropertyPath | None = None,
) -> ReconciliationAssessment:
    """Assess whether a conflict result may feed a Collibra mutation target."""
    object_identity = identity if identity is not None else result.object_identity
    path = property_path if property_path is not None else result.property_path

    if target_field_for_path(path) is None or not path_applicable_to_identity(
        path, object_identity
    ):
        return ReconciliationAssessment(
            applicable=False,
            safe=False,
            reason=REASON_NOT_MAPPED,
        )

    if result.state == "UNRESOLVED_CONFLICT":
        return ReconciliationAssessment(
            applicable=True,
            safe=False,
            reason=REASON_UNRESOLVED,
        )
    if result.state == "INVALID_OR_AMBIGUOUS_AUTHORITY":
        return ReconciliationAssessment(
            applicable=True,
            safe=False,
            reason=REASON_INVALID,
        )

    if result.state not in SAFE_STATES:
        return ReconciliationAssessment(
            applicable=True,
            safe=False,
            reason=REASON_UNRESOLVED,
        )

    if not result.has_effective_value:
        return ReconciliationAssessment(
            applicable=True,
            safe=False,
            reason=REASON_UNSUPPORTED,
        )

    converted = convert_effective_value(
        path=path,
        identity=object_identity,
        value=result.effective_value,
        has_effective_value=True,
    )
    if converted is None:
        return ReconciliationAssessment(
            applicable=True,
            safe=False,
            reason=REASON_UNSUPPORTED,
        )
    return ReconciliationAssessment(applicable=True, safe=True, reason=REASON_SAFE)
