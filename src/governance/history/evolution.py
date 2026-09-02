"""History evolution transition helpers (snapshot + orthogonal context)."""

from __future__ import annotations

from typing import Any

from governance.comparison.projection import (
    ComparisonObjectIdentity,
    ProjectedSnapshot,
)
from governance.domain.conflicts import PropertyConflictReport, PropertyConflictResult
from governance.domain.graph import GraphNodeIdentity
from governance.domain.observations import PropertyObservationSet, PropertyPath
from governance.history.load import ResolvedEntryState
from governance.identity.json_values import canonical_value_fingerprint


def snapshot_property_state(
    projected: ProjectedSnapshot,
    identity: ComparisonObjectIdentity,
    property_path: str,
) -> dict[str, Any]:
    """Lookup a comparable property value on a projected snapshot."""
    obj = projected.objects.get(identity)
    if obj is None:
        return {"has_value": False}
    prop = obj.properties.get(property_path)
    if prop is None:
        return {"has_value": False}
    return prop.to_dict()


def _availability_side(state: ResolvedEntryState) -> dict[str, bool]:
    form = state.entry.state.context_form()
    provenance = form in {"provenance", "full"}
    full = form == "full"
    return {
        "snapshot": True,
        "provenance": provenance,
        "authority_decision": full,
        "conflict": full,
    }


def build_availability(
    baseline: ResolvedEntryState,
    candidate: ResolvedEntryState,
) -> dict[str, Any]:
    left = _availability_side(baseline)
    right = _availability_side(candidate)
    return {
        "authority_decision": {
            "baseline": left["authority_decision"],
            "candidate": right["authority_decision"],
        },
        "conflict": {
            "baseline": left["conflict"],
            "candidate": right["conflict"],
        },
        "provenance": {
            "baseline": left["provenance"],
            "candidate": right["provenance"],
        },
        "snapshot": {
            "baseline": left["snapshot"],
            "candidate": right["snapshot"],
        },
    }


def find_object_change(
    comparison: dict[str, Any],
    identity: ComparisonObjectIdentity,
) -> dict[str, Any] | None:
    target = identity.to_dict()
    for item in comparison.get("object_changes", []):
        if item.get("object_identity") == target:
            return item
    return None


def build_snapshot_branch(
    *,
    comparison: dict[str, Any],
    baseline: ResolvedEntryState,
    candidate: ResolvedEntryState,
    queried_object: ComparisonObjectIdentity | None,
    queried_property: str | None,
) -> dict[str, Any]:
    if queried_object is None:
        return {"object_change": None, "property": None}

    object_change = find_object_change(comparison, queried_object)
    property_payload: dict[str, Any] | None = None
    if queried_property is not None:
        in_baseline = queried_object in baseline.projected.objects
        in_candidate = queried_object in candidate.projected.objects
        material = object_change is not None
        if in_baseline or in_candidate or material:
            change_type = object_change["change"] if object_change is not None else None

            if change_type == "changed":
                matched = None
                for prop_change in object_change["property_changes"]:
                    if prop_change["property"] == queried_property:
                        matched = prop_change
                        break
                if matched is not None:
                    baseline_value = matched["baseline"]
                    candidate_value = matched["candidate"]
                else:
                    baseline_value = snapshot_property_state(
                        baseline.projected, queried_object, queried_property
                    )
                    candidate_value = snapshot_property_state(
                        candidate.projected, queried_object, queried_property
                    )
            elif change_type == "added":
                baseline_value = {"has_value": False}
                candidate_value = snapshot_property_state(
                    candidate.projected, queried_object, queried_property
                )
            elif change_type == "removed":
                baseline_value = snapshot_property_state(
                    baseline.projected, queried_object, queried_property
                )
                candidate_value = {"has_value": False}
            else:
                baseline_value = snapshot_property_state(
                    baseline.projected, queried_object, queried_property
                )
                candidate_value = snapshot_property_state(
                    candidate.projected, queried_object, queried_property
                )

            property_payload = {
                "baseline": baseline_value,
                "candidate": candidate_value,
                "property": queried_property,
            }

    return {
        "object_change": object_change,
        "property": property_payload,
    }


def _observations_for(
    observations: PropertyObservationSet | None,
    object_identity: GraphNodeIdentity,
    property_path: PropertyPath,
) -> list[dict[str, Any]]:
    if observations is None:
        return []
    groups = [
        item.to_value_group_dict()
        for item in observations.observations
        if item.object_identity == object_identity and item.property_path == property_path
    ]
    groups.sort(key=lambda item: canonical_value_fingerprint(item["value"]))
    return groups


def provenance_projection(
    observations: PropertyObservationSet | None,
    object_identity: GraphNodeIdentity,
    property_path: PropertyPath,
) -> dict[str, Any]:
    return {
        "value_groups": _observations_for(observations, object_identity, property_path),
    }


def _find_conflict_result(
    report: PropertyConflictReport | None,
    object_identity: GraphNodeIdentity,
    property_path: PropertyPath,
) -> PropertyConflictResult | None:
    if report is None:
        return None
    for item in report.results:
        if item.object_identity == object_identity and item.property_path == property_path:
            return item
    return None


def conflict_state_projection(result: PropertyConflictResult | None) -> dict[str, Any]:
    if result is None:
        return {"has_result": False}
    return {
        "has_result": True,
        "reason": result.reason,
        "state": result.state,
    }


_AUTHORITY_APPLICABLE_REASONS = frozenset(
    {
        "NO_AUTHORITY_RULE",
        "INVALID_OR_AMBIGUOUS_AUTHORITY",
        "AUTHORITATIVE_SOURCE_NOT_OBSERVED",
        "AUTHORITATIVE_SOURCE_CONFLICTED",
        "RESOLVED_BY_AUTHORITY",
    }
)


def authority_decision_projection(result: PropertyConflictResult | None) -> dict[str, Any]:
    if result is None:
        return {"has_result": False}
    # Authority was not consulted for SINGLE_OBSERVATION / AGREEMENT (and any
    # non-authority ConflictReason). Orthogonal to conflict state/reason.
    if result.reason not in _AUTHORITY_APPLICABLE_REASONS:
        return {"has_result": True, "authority_applicable": False}
    winning = None if result.winning_rule_key is None else result.winning_rule_key.to_dict()
    return {
        "has_result": True,
        "authority_applicable": True,
        "outcome": result.reason,
        "winning_rule_key": winning,
    }


def effective_value_projection(result: PropertyConflictResult | None) -> dict[str, Any]:
    if result is None:
        return {"has_result": False}
    if not result.has_effective_value:
        return {"has_result": True, "has_effective_value": False}
    return {
        "has_effective_value": True,
        "has_result": True,
        "value": result.effective_value,
    }


def _dimension_block(
    *,
    baseline_available: bool,
    candidate_available: bool,
    baseline_projection: dict[str, Any] | None,
    candidate_projection: dict[str, Any] | None,
) -> dict[str, Any]:
    available = {"baseline": baseline_available, "candidate": candidate_available}
    change = None
    if (
        baseline_available
        and candidate_available
        and baseline_projection is not None
        and candidate_projection is not None
        and canonical_value_fingerprint(baseline_projection)
        != canonical_value_fingerprint(candidate_projection)
    ):
        change = {"baseline": baseline_projection, "candidate": candidate_projection}
    return {"available": available, "change": change}


def paths_for_governance_object(
    state: ResolvedEntryState,
    object_identity: GraphNodeIdentity,
) -> set[str]:
    paths: set[str] = set()
    if state.observations is not None:
        for item in state.observations.observations:
            if item.object_identity == object_identity:
                paths.add(item.property_path.to_pointer())
    if state.conflicts is not None:
        for item in state.conflicts.results:
            if item.object_identity == object_identity:
                paths.add(item.property_path.to_pointer())
    return paths


def object_known_in_context(
    state: ResolvedEntryState,
    object_identity: GraphNodeIdentity,
) -> bool:
    return bool(paths_for_governance_object(state, object_identity))


def build_context_property_changes(
    *,
    baseline: ResolvedEntryState,
    candidate: ResolvedEntryState,
    queried_governance_object: GraphNodeIdentity | None,
    queried_property: str | None,
) -> list[dict[str, Any]]:
    if queried_governance_object is None:
        return []

    if queried_property is not None:
        pointers = [queried_property]
        include_unchanged = True
    else:
        pointers = sorted(
            paths_for_governance_object(baseline, queried_governance_object)
            | paths_for_governance_object(candidate, queried_governance_object)
        )
        include_unchanged = False

    results: list[dict[str, Any]] = []
    base_avail = _availability_side(baseline)
    cand_avail = _availability_side(candidate)

    for pointer in pointers:
        property_path = PropertyPath.parse(pointer)

        base_prov = (
            provenance_projection(baseline.observations, queried_governance_object, property_path)
            if base_avail["provenance"]
            else None
        )
        cand_prov = (
            provenance_projection(candidate.observations, queried_governance_object, property_path)
            if cand_avail["provenance"]
            else None
        )
        provenance = _dimension_block(
            baseline_available=base_avail["provenance"],
            candidate_available=cand_avail["provenance"],
            baseline_projection=base_prov,
            candidate_projection=cand_prov,
        )

        base_conflict_result = (
            _find_conflict_result(baseline.conflicts, queried_governance_object, property_path)
            if base_avail["conflict"]
            else None
        )
        cand_conflict_result = (
            _find_conflict_result(candidate.conflicts, queried_governance_object, property_path)
            if cand_avail["conflict"]
            else None
        )

        base_conflict = (
            conflict_state_projection(base_conflict_result) if base_avail["conflict"] else None
        )
        cand_conflict = (
            conflict_state_projection(cand_conflict_result) if cand_avail["conflict"] else None
        )
        conflict = _dimension_block(
            baseline_available=base_avail["conflict"],
            candidate_available=cand_avail["conflict"],
            baseline_projection=base_conflict,
            candidate_projection=cand_conflict,
        )

        base_authority = (
            authority_decision_projection(base_conflict_result)
            if base_avail["authority_decision"]
            else None
        )
        cand_authority = (
            authority_decision_projection(cand_conflict_result)
            if cand_avail["authority_decision"]
            else None
        )
        authority_decision = _dimension_block(
            baseline_available=base_avail["authority_decision"],
            candidate_available=cand_avail["authority_decision"],
            baseline_projection=base_authority,
            candidate_projection=cand_authority,
        )

        base_effective = (
            effective_value_projection(base_conflict_result)
            if base_avail["authority_decision"]
            else None
        )
        cand_effective = (
            effective_value_projection(cand_conflict_result)
            if cand_avail["authority_decision"]
            else None
        )
        effective_value = _dimension_block(
            baseline_available=base_avail["authority_decision"],
            candidate_available=cand_avail["authority_decision"],
            baseline_projection=base_effective,
            candidate_projection=cand_effective,
        )

        entry = {
            "authority_decision": authority_decision,
            "conflict": conflict,
            "effective_value": effective_value,
            "property": pointer,
            "provenance": provenance,
        }
        materially_changed = any(
            block["change"] is not None
            for block in (provenance, conflict, authority_decision, effective_value)
        )
        if include_unchanged or materially_changed:
            results.append(entry)

    return results


def build_transition(
    *,
    from_index: int,
    to_index: int,
    baseline: ResolvedEntryState,
    candidate: ResolvedEntryState,
    comparison: dict[str, Any],
    queried_object: ComparisonObjectIdentity | None,
    queried_governance_object: GraphNodeIdentity | None,
    queried_property: str | None,
) -> dict[str, Any]:
    return {
        "availability": build_availability(baseline, candidate),
        "context_property_changes": build_context_property_changes(
            baseline=baseline,
            candidate=candidate,
            queried_governance_object=queried_governance_object,
            queried_property=queried_property,
        ),
        "from_entry_state_identity": baseline.entry.state.entry_state_identity().to_dict(),
        "from_index": from_index,
        "from_snapshot_identity": baseline.entry.state.snapshot.to_dict(),
        "snapshot": build_snapshot_branch(
            comparison=comparison,
            baseline=baseline,
            candidate=candidate,
            queried_object=queried_object,
            queried_property=queried_property,
        ),
        "to_entry_state_identity": candidate.entry.state.entry_state_identity().to_dict(),
        "to_index": to_index,
        "to_snapshot_identity": candidate.entry.state.snapshot.to_dict(),
    }


__all__ = [
    "authority_decision_projection",
    "build_availability",
    "build_context_property_changes",
    "build_snapshot_branch",
    "build_transition",
    "conflict_state_projection",
    "effective_value_projection",
    "find_object_change",
    "object_known_in_context",
    "paths_for_governance_object",
    "provenance_projection",
    "snapshot_property_state",
]
