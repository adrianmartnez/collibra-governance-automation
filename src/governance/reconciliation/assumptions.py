"""Reconciliation assumptions contract, identity, and boundary builders."""

from __future__ import annotations

from typing import Any

from governance.domain.conflicts import PropertyConflictReport, PropertyConflictResult
from governance.domain.graph import GraphNodeIdentity
from governance.domain.observations import PropertyPath
from governance.identity.hashing import ContentIdentity, reconciliation_assumptions_identity
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.integrations.collibra.models import (
    CollibraAssetSpec,
    CollibraDesiredState,
    CollibraRemoteAsset,
    CollibraRemoteState,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
)
from governance.reconciliation.errors import (
    CODE_INVALID_OR_AMBIGUOUS_AUTHORITY,
    CODE_UNRESOLVED_PROPERTY_CONFLICT,
    CODE_UNSUPPORTED_EFFECTIVE_VALUE,
    DiagnosticError,
    ReconciliationError,
)
from governance.reconciliation.overlay import get_asset_attr, get_remote_attr
from governance.reconciliation.physical_index import PhysicalReconciliationIndex
from governance.reconciliation.safety import assess_reconciliation
from governance.reconciliation.targets import (
    mapped_property_paths_for_kind,
    target_field_for_path,
)

RECONCILIATION_ASSUMPTIONS_SCHEMA = "governance-reconciliation-assumptions"
RECONCILIATION_ASSUMPTIONS_VERSION = "1"

ROLE_MUTATION = "mutation"
ROLE_OVERLAY = "overlay"


def decision_from_result(result: PropertyConflictResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reason": result.reason,
        "state": result.state,
        "value_groups": [group.to_value_group_dict() for group in result.value_groups],
    }
    if result.has_effective_value:
        payload["effective_value"] = result.effective_value
    if result.winning_rule_key is not None:
        payload["winning_rule_key"] = result.winning_rule_key.to_dict()
    return payload


def empty_assumptions() -> dict[str, Any]:
    return {
        "actions": [],
        "assumptions_schema": RECONCILIATION_ASSUMPTIONS_SCHEMA,
        "assumptions_version": RECONCILIATION_ASSUMPTIONS_VERSION,
    }


def assumptions_content_identity(assumptions: dict[str, Any]) -> ContentIdentity:
    return reconciliation_assumptions_identity(assumptions)


def _result_lookup(
    report: PropertyConflictReport,
) -> dict[tuple[bytes, str], PropertyConflictResult]:
    return {
        (item.object_identity.canonical_bytes(), item.property_path.to_pointer()): item
        for item in report.results
    }


def _roles(*values: str) -> list[str]:
    return sorted(set(values))


def _property_entry(
    *,
    identity: GraphNodeIdentity,
    path: PropertyPath,
    roles: list[str],
    result: PropertyConflictResult | None,
) -> dict[str, Any]:
    return {
        "decision": None if result is None else decision_from_result(result),
        "object": identity.to_dict(),
        "property": path.to_pointer(),
        "roles": roles,
    }


def _attr_changed(
    baseline: CollibraAssetSpec,
    reconciled: CollibraAssetSpec,
    remote: CollibraRemoteAsset | None,
    *,
    mapping_config: CollibraMappingConfig,
    field: str,
) -> tuple[bool, bool]:
    """Return (mutation_material vs remote on reconciled, overlay_changed baseline→reconciled)."""
    base_val = get_asset_attr(baseline, mapping_config=mapping_config, field=field)
    rec_val = get_asset_attr(reconciled, mapping_config=mapping_config, field=field)
    remote_val = get_remote_attr(remote, mapping_config=mapping_config, field=field)
    overlay_changed = base_val != rec_val
    mutation_material = rec_val != remote_val
    return mutation_material, overlay_changed


def build_reconciliation_assumptions(
    *,
    baseline_desired: CollibraDesiredState,
    reconciled_desired: CollibraDesiredState,
    remote_state: CollibraRemoteState,
    sync_plan: SyncPlan,
    conflict_report: PropertyConflictReport,
    mapping_config: CollibraMappingConfig,
    physical_index: PhysicalReconciliationIndex,
) -> dict[str, Any]:
    """Build canonical assumptions from plan/desired/remote (independent of source flags)."""
    baseline_by_id = {asset.local_id: asset for asset in baseline_desired.assets}
    reconciled_by_id = {asset.local_id: asset for asset in reconciled_desired.assets}
    remote_by_id = {asset.local_id: asset for asset in remote_state.assets if asset.local_id}
    results = _result_lookup(conflict_report)

    action_entries: list[dict[str, Any]] = []

    for action in sync_plan.actions:
        if action.object_kind is not SyncObjectKind.ASSET:
            continue
        if action.action_type is SyncActionType.REMOTE_ONLY:
            continue
        local_id = action.local_id
        if not local_id:
            continue
        identity = physical_index.get(local_id)
        if identity is None:
            continue
        baseline = baseline_by_id.get(local_id)
        reconciled = reconciled_by_id.get(local_id)
        if baseline is None or reconciled is None:
            continue
        remote = remote_by_id.get(local_id)

        properties: list[dict[str, Any]] = []

        if action.action_type is SyncActionType.CREATE:
            for path in mapped_property_paths_for_kind(identity.kind):
                result = results.get((identity.canonical_bytes(), path.to_pointer()))
                properties.append(
                    _property_entry(
                        identity=identity,
                        path=path,
                        roles=_roles(ROLE_MUTATION),
                        result=result,
                    )
                )
        elif action.action_type is SyncActionType.UPDATE:
            for path in mapped_property_paths_for_kind(identity.kind):
                field = target_field_for_path(path)
                if field is None:
                    continue
                mutation_material, overlay_changed = _attr_changed(
                    baseline,
                    reconciled,
                    remote,
                    mapping_config=mapping_config,
                    field=field,
                )
                if not mutation_material and not overlay_changed:
                    continue
                roles: list[str] = []
                if mutation_material:
                    roles.append(ROLE_MUTATION)
                if overlay_changed:
                    roles.append(ROLE_OVERLAY)
                result = results.get((identity.canonical_bytes(), path.to_pointer()))
                properties.append(
                    _property_entry(
                        identity=identity,
                        path=path,
                        roles=_roles(*roles),
                        result=result,
                    )
                )
        elif action.action_type is SyncActionType.UNCHANGED:
            for path in mapped_property_paths_for_kind(identity.kind):
                field = target_field_for_path(path)
                if field is None:
                    continue
                _mutation_material, overlay_changed = _attr_changed(
                    baseline,
                    reconciled,
                    remote,
                    mapping_config=mapping_config,
                    field=field,
                )
                if not overlay_changed:
                    continue
                result = results.get((identity.canonical_bytes(), path.to_pointer()))
                properties.append(
                    _property_entry(
                        identity=identity,
                        path=path,
                        roles=_roles(ROLE_OVERLAY),
                        result=result,
                    )
                )

        if not properties:
            continue
        properties.sort(
            key=lambda item: (
                GraphNodeIdentity(
                    namespace=item["object"]["namespace"],
                    kind=item["object"]["kind"],
                    logical_id=item["object"]["logical_id"],
                    parent=_parent_from_dict(item["object"].get("parent")),
                ).canonical_bytes(),
                item["property"],
            )
        )
        action_entries.append(
            {
                "action_type": action.action_type.value,
                "local_id": local_id,
                "object_kind": "asset",
                "properties": properties,
            }
        )

    action_entries.sort(
        key=lambda item: (item["object_kind"], item["local_id"], item["action_type"])
    )
    return {
        "actions": action_entries,
        "assumptions_schema": RECONCILIATION_ASSUMPTIONS_SCHEMA,
        "assumptions_version": RECONCILIATION_ASSUMPTIONS_VERSION,
    }


def _parent_from_dict(payload: Any) -> GraphNodeIdentity | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise TypeError("parent must be a mapping or null")
    return GraphNodeIdentity(
        namespace=str(payload["namespace"]),
        kind=str(payload["kind"]),
        logical_id=str(payload["logical_id"]),
        parent=_parent_from_dict(payload.get("parent")),
    )


def identity_from_saved_object(payload: dict[str, Any]) -> GraphNodeIdentity:
    return GraphNodeIdentity(
        namespace=str(payload["namespace"]),
        kind=str(payload["kind"]),
        logical_id=str(payload["logical_id"]),
        parent=_parent_from_dict(payload.get("parent")),
    )


def recompute_assumptions_on_saved_boundary(
    *,
    saved_assumptions: dict[str, Any],
    conflict_report: PropertyConflictReport,
) -> dict[str, Any]:
    """Rebuild assumptions using saved boundary structure and current decisions."""
    results = _result_lookup(conflict_report)
    actions_out: list[dict[str, Any]] = []
    for action in saved_assumptions.get("actions") or []:
        properties_out: list[dict[str, Any]] = []
        for prop in action.get("properties") or []:
            identity = identity_from_saved_object(prop["object"])
            pointer = str(prop["property"])
            result = results.get((identity.canonical_bytes(), pointer))
            properties_out.append(
                {
                    "decision": None if result is None else decision_from_result(result),
                    "object": prop["object"],
                    "property": pointer,
                    "roles": list(prop["roles"]),
                }
            )
        properties_out.sort(
            key=lambda item: (
                identity_from_saved_object(item["object"]).canonical_bytes(),
                item["property"],
            )
        )
        actions_out.append(
            {
                "action_type": action["action_type"],
                "local_id": action["local_id"],
                "object_kind": action["object_kind"],
                "properties": properties_out,
            }
        )
    actions_out.sort(key=lambda item: (item["object_kind"], item["local_id"], item["action_type"]))
    return {
        "actions": actions_out,
        "assumptions_schema": RECONCILIATION_ASSUMPTIONS_SCHEMA,
        "assumptions_version": RECONCILIATION_ASSUMPTIONS_VERSION,
    }


def validate_assumptions_safety(
    assumptions: dict[str, Any],
    conflict_report: PropertyConflictReport,
) -> None:
    """Block plan generation when material boundary decisions are unsafe/unrepresentable."""
    results = _result_lookup(conflict_report)
    errors: list[DiagnosticError] = []
    for action_index, action in enumerate(assumptions.get("actions") or []):
        for prop_index, prop in enumerate(action.get("properties") or []):
            if prop.get("decision") is None:
                continue
            identity = identity_from_saved_object(prop["object"])
            pointer = str(prop["property"])
            result = results.get((identity.canonical_bytes(), pointer))
            if result is None:
                continue
            assessment = assess_reconciliation(result)
            if assessment.applicable and not assessment.safe:
                if assessment.reason == "unsupported_effective_value":
                    code = CODE_UNSUPPORTED_EFFECTIVE_VALUE
                elif assessment.reason == "invalid_or_ambiguous_authority":
                    code = CODE_INVALID_OR_AMBIGUOUS_AUTHORITY
                else:
                    code = CODE_UNRESOLVED_PROPERTY_CONFLICT
                errors.append(
                    DiagnosticError(
                        code=code,
                        path=f"/actions/{action_index}/properties/{prop_index}",
                        message=f"reconciliation blocked: {assessment.reason}",
                    )
                )
    if errors:
        raise ReconciliationError(errors)
