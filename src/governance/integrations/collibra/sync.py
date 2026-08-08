"""Plan-driven Collibra synchronization (dry-run by default, no deletes)."""

from __future__ import annotations

from governance.integrations.collibra.adapters import CollibraAdapter, CollibraAdapterError
from governance.integrations.collibra.models import (
    CollibraAssetSpec,
    CollibraDesiredState,
    CollibraRemoteAsset,
    CollibraRemoteState,
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
    SyncResult,
)

# Compatibility boundary for saved-plan planning/mapping/execution semantics.
PLANNER_CONTRACT_VERSION = "1"

_ASSET_CREATE_ORDER = ("db:", "sch:", "tbl:", "col:")


def build_sync_plan(
    desired: CollibraDesiredState,
    remote: CollibraRemoteState,
) -> SyncPlan:
    """Compare desired and managed remote state into an explicit plan.

    Unmanaged remote objects are expected to be excluded from ``remote`` by the
    adapter. REMOTE_ONLY means a managed remote asset/relationship whose local
    identity is absent from desired state. DELETE is never produced.
    """
    actions: list[SyncAction] = []
    remote_assets = {asset.local_id: asset for asset in remote.assets}
    desired_assets = {asset.local_id: asset for asset in desired.assets}

    for local_id, desired_asset in desired_assets.items():
        remote_asset = remote_assets.get(local_id)
        if remote_asset is None:
            actions.append(
                SyncAction(
                    action_type=SyncActionType.CREATE,
                    object_kind=SyncObjectKind.ASSET,
                    local_id=local_id,
                    reason="desired asset has no managed remote match",
                    desired_asset=desired_asset,
                )
            )
            continue
        changed_fields = _asset_changed_fields(desired_asset, remote_asset)
        if not changed_fields:
            actions.append(
                SyncAction(
                    action_type=SyncActionType.UNCHANGED,
                    object_kind=SyncObjectKind.ASSET,
                    local_id=local_id,
                    remote_id=remote_asset.remote_id,
                    reason="supported asset state equivalent",
                    desired_asset=desired_asset,
                )
            )
        else:
            actions.append(
                SyncAction(
                    action_type=SyncActionType.UPDATE,
                    object_kind=SyncObjectKind.ASSET,
                    local_id=local_id,
                    remote_id=remote_asset.remote_id,
                    reason="supported asset metadata differs",
                    desired_asset=desired_asset,
                    changed_fields=changed_fields,
                )
            )

    for local_id, remote_asset in remote_assets.items():
        if local_id not in desired_assets:
            actions.append(
                SyncAction(
                    action_type=SyncActionType.REMOTE_ONLY,
                    object_kind=SyncObjectKind.ASSET,
                    local_id=local_id,
                    remote_id=remote_asset.remote_id,
                    reason="managed remote asset absent from desired state",
                )
            )

    remote_relationships = {
        _relationship_key(rel.source_local_id, rel.target_local_id, rel.relation_type_ref): rel
        for rel in remote.relationships
    }
    desired_relationships = {
        _relationship_key(
            rel.source_local_id,
            rel.target_local_id,
            rel.relation_type_ref,
        ): rel
        for rel in desired.relationships
    }

    for key, desired_rel in desired_relationships.items():
        remote_rel = remote_relationships.get(key)
        if remote_rel is None:
            actions.append(
                SyncAction(
                    action_type=SyncActionType.CREATE,
                    object_kind=SyncObjectKind.RELATIONSHIP,
                    local_id=desired_rel.local_key,
                    reason="desired relationship missing remotely",
                    desired_relationship=desired_rel,
                )
            )
        else:
            actions.append(
                SyncAction(
                    action_type=SyncActionType.UNCHANGED,
                    object_kind=SyncObjectKind.RELATIONSHIP,
                    local_id=desired_rel.local_key,
                    remote_id=remote_rel.remote_id,
                    reason="managed relationship equivalent",
                    desired_relationship=desired_rel,
                )
            )

    for key, remote_rel in remote_relationships.items():
        if key not in desired_relationships:
            actions.append(
                SyncAction(
                    action_type=SyncActionType.REMOTE_ONLY,
                    object_kind=SyncObjectKind.RELATIONSHIP,
                    local_id=remote_rel.local_key,
                    remote_id=remote_rel.remote_id,
                    reason="managed remote relationship absent from desired state",
                )
            )

    return SyncPlan(actions=tuple(actions))


def execute_sync_plan(
    adapter: CollibraAdapter,
    plan: SyncPlan,
    *,
    apply: bool = False,
) -> SyncResult:
    """Execute an explicit plan. Dry-run (apply=False) performs zero writes.

    The executor does not recompute the plan. Write failures fail-fast.
    """
    unchanged_count = len(plan.unchanged)
    mutating = [
        action
        for action in plan.actions
        if action.action_type in {SyncActionType.CREATE, SyncActionType.UPDATE}
    ]
    if not apply:
        return SyncResult(
            success=True,
            dry_run=True,
            applied_count=0,
            unchanged_count=unchanged_count,
            plan=plan,
        )

    local_to_remote = _seed_remote_ids_from_plan(plan)
    applied = 0

    for action in _ordered_mutating_actions(mutating):
        try:
            if action.object_kind is SyncObjectKind.ASSET:
                if action.action_type is SyncActionType.CREATE:
                    assert action.desired_asset is not None
                    remote_id = adapter.create_asset(action.desired_asset)
                    local_to_remote[action.desired_asset.local_id] = remote_id
                    applied += 1
                elif action.action_type is SyncActionType.UPDATE:
                    assert action.desired_asset is not None and action.remote_id is not None
                    adapter.update_asset(
                        action.remote_id,
                        action.desired_asset,
                        patch_name="name" in action.changed_fields,
                        patch_display_name="display_name" in action.changed_fields,
                    )
                    local_to_remote[action.desired_asset.local_id] = action.remote_id
                    applied += 1
            elif (
                action.object_kind is SyncObjectKind.RELATIONSHIP
                and action.action_type is SyncActionType.CREATE
            ):
                assert action.desired_relationship is not None
                relationship = action.desired_relationship
                source_remote = local_to_remote.get(relationship.source_local_id)
                target_remote = local_to_remote.get(relationship.target_local_id)
                if source_remote is None or target_remote is None:
                    raise CollibraAdapterError(
                        "relationship endpoints unresolved",
                        operation="create_relationship",
                        endpoint_path="/sync/relationships",
                    )
                adapter.create_relationship(
                    relationship,
                    source_remote_id=source_remote,
                    target_remote_id=target_remote,
                )
                applied += 1
        except CollibraAdapterError as exc:
            return SyncResult(
                success=False,
                dry_run=False,
                applied_count=applied,
                unchanged_count=unchanged_count,
                failed_action=action,
                error=str(exc),
                plan=plan,
            )

    return SyncResult(
        success=True,
        dry_run=False,
        applied_count=applied,
        unchanged_count=unchanged_count,
        plan=plan,
    )


def _asset_changed_fields(
    desired: CollibraAssetSpec,
    remote: CollibraRemoteAsset,
) -> tuple[str, ...]:
    changed: list[str] = []
    if desired.name != remote.name:
        changed.append("name")
    if (desired.display_name or None) != (remote.display_name or None):
        changed.append("display_name")
    desired_attrs = {attr.attribute_type_ref: attr.value for attr in desired.attributes}
    remote_attrs = {attr.attribute_type_ref: attr.value for attr in remote.managed_attributes}
    if desired_attrs != remote_attrs:
        changed.append("managed_attributes")
    return tuple(changed)


def _relationship_key(
    source_local_id: str,
    target_local_id: str,
    relation_type_ref: str,
) -> tuple[str, str, str]:
    return (source_local_id, target_local_id, relation_type_ref)


def _seed_remote_ids_from_plan(plan: SyncPlan) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for action in plan.actions:
        if (
            action.object_kind is SyncObjectKind.ASSET
            and action.local_id
            and action.remote_id
            and action.action_type
            in {SyncActionType.UPDATE, SyncActionType.UNCHANGED, SyncActionType.REMOTE_ONLY}
        ):
            mapping[action.local_id] = action.remote_id
    return mapping


def _ordered_mutating_actions(actions: list[SyncAction]) -> list[SyncAction]:
    asset_creates = [
        action
        for action in actions
        if action.action_type is SyncActionType.CREATE
        and action.object_kind is SyncObjectKind.ASSET
    ]
    asset_updates = [
        action
        for action in actions
        if action.action_type is SyncActionType.UPDATE
        and action.object_kind is SyncObjectKind.ASSET
    ]
    relationship_creates = [
        action
        for action in actions
        if action.action_type is SyncActionType.CREATE
        and action.object_kind is SyncObjectKind.RELATIONSHIP
    ]

    def asset_rank(action: SyncAction) -> tuple[int, str]:
        local_id = action.local_id or ""
        for index, prefix in enumerate(_ASSET_CREATE_ORDER):
            if local_id.startswith(prefix):
                return (index, local_id)
        return (len(_ASSET_CREATE_ORDER), local_id)

    asset_creates.sort(key=asset_rank)
    asset_updates.sort(key=lambda action: action.local_id or "")
    relationship_creates.sort(key=lambda action: action.local_id or "")
    return asset_creates + asset_updates + relationship_creates
