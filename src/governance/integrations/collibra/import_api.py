"""Plan-faithful Collibra Import API v2 compilation and json-job submit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from governance.identity.canonicalize import canonical_json_bytes
from governance.integrations.collibra.adapters import CollibraAdapterError
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.integrations.collibra.models import (
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
)
from governance.integrations.collibra.sync import _ordered_mutating_actions

IMPORT_JSON_JOB_PATH = "/rest/2.0/import/json-job"
FORBIDDEN_SYNC_PATH_FRAGMENT = "/import/synchronize"

IMPORT_MULTIPART_FIELDS = {
    "continueOnError": "false",
    "relationsAction": "ADD_OR_IGNORE",
    "attributesAction": "REPLACE",
    "simulation": "false",
    "sendNotification": "false",
    "deleteFile": "false",
}


class ImportCompileError(CollibraAdapterError):
    """Import document cannot represent the reviewed plan safely."""

    def __init__(self, message: str) -> None:
        super().__init__(message, operation="compile_import", endpoint_path=IMPORT_JSON_JOB_PATH)


@dataclass(frozen=True, slots=True)
class ImportCommand:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True, slots=True)
class ImportDocument:
    commands: tuple[ImportCommand, ...]

    def to_list(self) -> list[dict[str, Any]]:
        return [command.to_dict() for command in self.commands]

    def canonical_json(self) -> bytes:
        return canonical_json_bytes(self.to_list())


@dataclass(frozen=True, slots=True)
class ImportSubmission:
    job_id: str


def compile_import_document(
    plan: SyncPlan,
    mapping_config: CollibraMappingConfig,
) -> ImportDocument:
    """Translate a reviewed SyncPlan into Import v2 commands.

    Emits only mapping-managed attribute types. Unmanaged types are rejected
    before any HTTP. Relations are attached to the source asset command.
    """
    allowed_attr_types = set(mapping_config.attribute_type_refs.values())
    mutating = [
        action
        for action in plan.actions
        if action.action_type in {SyncActionType.CREATE, SyncActionType.UPDATE}
    ]
    if any(
        action.object_kind is SyncObjectKind.RELATIONSHIP
        and action.action_type is SyncActionType.UPDATE
        for action in plan.actions
    ):
        raise ImportCompileError("import_v2 cannot represent relationship UPDATE/DELETE")

    assets_by_local = _asset_lookup(plan)
    commands: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for action in _ordered_mutating_actions(mutating):
        if action.object_kind is SyncObjectKind.ASSET:
            command = _asset_command(action, allowed_attr_types=allowed_attr_types)
            local_id = action.desired_asset.local_id if action.desired_asset else action.local_id
            if not local_id:
                raise ImportCompileError("asset command missing local identity")
            if local_id in commands:
                raise ImportCompileError("duplicate import identifier")
            commands[local_id] = command
            order.append(local_id)
        elif action.object_kind is SyncObjectKind.RELATIONSHIP:
            if action.action_type is not SyncActionType.CREATE:
                raise ImportCompileError("import_v2 cannot represent relationship UPDATE/DELETE")
            relationship = action.desired_relationship
            if relationship is None:
                raise ImportCompileError("relationship action missing spec")
            source_command = commands.get(relationship.source_local_id)
            if source_command is None:
                source_command = _relation_only_command(
                    plan,
                    source_local_id=relationship.source_local_id,
                )
                commands[relationship.source_local_id] = source_command
                order.append(relationship.source_local_id)
            _add_relation(
                source_command,
                relation_type_ref=relationship.relation_type_ref,
                target=_relation_target_identifier(
                    plan,
                    assets_by_local,
                    target_local_id=relationship.target_local_id,
                ),
            )

    return ImportDocument(
        commands=tuple(ImportCommand(payload=commands[key]) for key in order),
    )


def _asset_lookup(plan: SyncPlan) -> dict[str, SyncAction]:
    found: dict[str, SyncAction] = {}
    for action in plan.actions:
        if action.object_kind is SyncObjectKind.ASSET and action.local_id:
            found[action.local_id] = action
    return found


def _asset_command(
    action: SyncAction,
    *,
    allowed_attr_types: set[str],
) -> dict[str, Any]:
    asset = action.desired_asset
    if asset is None:
        raise ImportCompileError("asset action missing desired spec")
    command: dict[str, Any] = {"resourceType": "Asset"}
    if action.action_type is SyncActionType.CREATE:
        command["identifier"] = {
            "name": asset.name,
            "domain": {"id": asset.domain_ref},
        }
        command["type"] = {"id": asset.asset_type_ref}
        if asset.display_name is not None:
            command["displayName"] = asset.display_name
        attributes = _managed_attributes(asset.attributes, allowed_attr_types)
        if attributes:
            command["attributes"] = attributes
    elif action.action_type is SyncActionType.UPDATE:
        if not action.remote_id:
            raise ImportCompileError("UPDATE asset missing remote id")
        command["identifier"] = {"id": action.remote_id}
        if "name" in action.changed_fields:
            command["name"] = asset.name
        if "display_name" in action.changed_fields and asset.display_name is not None:
            command["displayName"] = asset.display_name
        if "managed_attributes" in action.changed_fields:
            attributes = _managed_attributes(asset.attributes, allowed_attr_types)
            if not attributes:
                raise ImportCompileError("import_v2 cannot represent managed attribute UPDATE")
            command["attributes"] = attributes
    else:
        raise ImportCompileError("unsupported asset action for import_v2")
    return command


def _managed_attributes(
    attributes: tuple[Any, ...], allowed: set[str]
) -> dict[str, list[dict[str, str]]]:
    seen: set[str] = set()
    payload: dict[str, list[dict[str, str]]] = {}
    for attribute in attributes:
        type_ref = attribute.attribute_type_ref
        if type_ref not in allowed:
            raise ImportCompileError("import_v2 cannot emit unmanaged attribute types")
        if type_ref in seen:
            raise ImportCompileError(
                "import_v2 cannot represent multiple values for one attribute type"
            )
        seen.add(type_ref)
        payload[type_ref] = [{"value": attribute.value}]
    return payload


def _relation_only_command(plan: SyncPlan, *, source_local_id: str) -> dict[str, Any]:
    remote_id = None
    for action in plan.actions:
        if (
            action.object_kind is SyncObjectKind.ASSET
            and action.local_id == source_local_id
            and action.remote_id
        ):
            remote_id = action.remote_id
            break
    if not remote_id:
        raise ImportCompileError("relationship source has no remote identifier")
    return {
        "resourceType": "Asset",
        "identifier": {"id": remote_id},
    }


def _relation_target_identifier(
    plan: SyncPlan,
    assets_by_local: dict[str, SyncAction],
    *,
    target_local_id: str,
) -> dict[str, Any]:
    action = assets_by_local.get(target_local_id)
    if action is not None and action.remote_id:
        return {"id": action.remote_id}
    if action is not None and action.desired_asset is not None:
        return {
            "name": action.desired_asset.name,
            "domain": {"id": action.desired_asset.domain_ref},
        }
    raise ImportCompileError("relationship target cannot be identified")


def _add_relation(
    command: dict[str, Any],
    *,
    relation_type_ref: str,
    target: dict[str, Any],
) -> None:
    relations = command.setdefault("relations", {})
    key = f"{relation_type_ref}:TARGET"
    bucket = relations.setdefault(key, [])
    bucket.append(target)


def execute_collibra_plan(
    adapter: Any,
    plan: SyncPlan,
    mapping_config: CollibraMappingConfig,
    *,
    apply: bool,
    execution_mode: str,
) -> Any:
    """Run Core REST or Import v2 for a reviewed plan. Mock always uses Core REST."""
    from governance.integrations.collibra.models import SyncResult
    from governance.integrations.collibra.sync import execute_sync_plan

    mode = (execution_mode or "core_rest").strip().lower()
    if mode == "core_rest" or getattr(adapter, "mode", None) == "mock":
        return execute_sync_plan(adapter, plan, apply=apply)
    if mode != "import_v2":
        raise ImportCompileError("collibra_execution_mode must be core_rest or import_v2")
    document = compile_import_document(plan, mapping_config)
    unchanged_count = len(plan.unchanged)
    if not apply:
        return SyncResult(
            success=True,
            dry_run=True,
            applied_count=0,
            unchanged_count=unchanged_count,
            plan=plan,
        )
    if not document.commands:
        return SyncResult(
            success=True,
            dry_run=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            plan=plan,
        )
    adapter.submit_json_import(document)
    return SyncResult(
        success=True,
        dry_run=False,
        applied_count=len(document.commands),
        unchanged_count=unchanged_count,
        plan=plan,
    )
