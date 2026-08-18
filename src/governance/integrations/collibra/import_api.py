"""Plan-faithful Collibra Import API v2 compilation and json-job submit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

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


class ImportCollisionError(CollibraAdapterError):
    """Import CREATE cannot be proven free of name+domain MERGE adoption."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            operation="import_collision_check",
            endpoint_path="/rest/2.0/assets",
            endpoint_family="core_rest",
        )


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


@dataclass(frozen=True, slots=True)
class ImportExecutionResult:
    """Compile + optional json-job submit. Not terminal Import job completion.

    ``submitted`` means POST /import/json-job accepted a job id. ``applied_count``
    stays 0 until a later lifecycle PR can observe job SUCCESS.
    """

    plan: SyncPlan
    document: ImportDocument
    dry_run: bool
    submitted: bool
    submission: ImportSubmission | None = None
    unchanged_count: int = 0
    error: str | None = None
    failed_action: SyncAction | None = None

    @property
    def job_id(self) -> str | None:
        if self.submission is None:
            return None
        return self.submission.job_id

    @property
    def applied_count(self) -> int:
        return 0

    @property
    def success(self) -> bool:
        return self.error is None


def compile_import_document(
    plan: SyncPlan,
    mapping_config: CollibraMappingConfig,
) -> ImportDocument:
    """Translate a reviewed SyncPlan into Import v2 commands.

    Emits only mapping-managed attribute types. Unmanaged types are rejected
    before any HTTP. Relationship CREATE is attached to the later endpoint
    command so Import never depends on a forward identifier.
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
            _attach_relationship(
                commands,
                order,
                plan,
                assets_by_local,
                source_local_id=relationship.source_local_id,
                target_local_id=relationship.target_local_id,
                relation_type_ref=relationship.relation_type_ref,
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


def _is_create_asset(action: SyncAction | None) -> bool:
    return (
        action is not None
        and action.object_kind is SyncObjectKind.ASSET
        and action.action_type is SyncActionType.CREATE
    )


def _asset_identifier(action: SyncAction) -> dict[str, Any]:
    if action.action_type is SyncActionType.CREATE:
        asset = action.desired_asset
        if asset is None:
            raise ImportCompileError("asset action missing desired spec")
        return {
            "name": asset.name,
            "domain": {"id": asset.domain_ref},
        }
    if action.remote_id:
        return {"id": action.remote_id}
    raise ImportCompileError("relationship endpoint cannot be identified")


def _ensure_relation_command(
    commands: dict[str, dict[str, Any]],
    order: list[str],
    plan: SyncPlan,
    *,
    local_id: str,
) -> dict[str, Any]:
    existing = commands.get(local_id)
    if existing is not None:
        return existing
    command = _relation_only_command(plan, source_local_id=local_id)
    commands[local_id] = command
    order.append(local_id)
    return command


def _attach_relationship(
    commands: dict[str, dict[str, Any]],
    order: list[str],
    plan: SyncPlan,
    assets_by_local: dict[str, SyncAction],
    *,
    source_local_id: str,
    target_local_id: str,
    relation_type_ref: str,
) -> None:
    source_action = assets_by_local.get(source_local_id)
    target_action = assets_by_local.get(target_local_id)
    if source_action is None or target_action is None:
        raise ImportCompileError("relationship endpoint cannot be identified")
    source_create = _is_create_asset(source_action)
    target_create = _is_create_asset(target_action)
    if source_create and target_create:
        try:
            source_idx = order.index(source_local_id)
            target_idx = order.index(target_local_id)
        except ValueError as exc:
            raise ImportCompileError("relationship CREATE missing asset command") from exc
        if target_idx > source_idx:
            _add_relation(
                commands[target_local_id],
                relation_type_ref=relation_type_ref,
                direction="SOURCE",
                related=_asset_identifier(source_action),
            )
        else:
            _add_relation(
                commands[source_local_id],
                relation_type_ref=relation_type_ref,
                direction="TARGET",
                related=_asset_identifier(target_action),
            )
        return
    if source_create:
        source_command = commands.get(source_local_id)
        if source_command is None:
            raise ImportCompileError("relationship CREATE missing asset command")
        _add_relation(
            source_command,
            relation_type_ref=relation_type_ref,
            direction="TARGET",
            related=_asset_identifier(target_action),
        )
        return
    if target_create:
        target_command = commands.get(target_local_id)
        if target_command is None:
            raise ImportCompileError("relationship CREATE missing asset command")
        _add_relation(
            target_command,
            relation_type_ref=relation_type_ref,
            direction="SOURCE",
            related=_asset_identifier(source_action),
        )
        return
    host = _ensure_relation_command(commands, order, plan, local_id=source_local_id)
    _add_relation(
        host,
        relation_type_ref=relation_type_ref,
        direction="TARGET",
        related=_asset_identifier(target_action),
    )


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


def _prove_create_identifiers_absent(adapter: Any, plan: SyncPlan) -> None:
    """Fail closed unless each CREATE name+domain is observably absent.

    Import MERGE identifies CREATE by name+domain, not managed ``local_id``.
    This proof is live-submit only; ``compile_import_document`` stays offline.
    """
    creates = [
        action
        for action in plan.actions
        if action.object_kind is SyncObjectKind.ASSET
        and action.action_type is SyncActionType.CREATE
    ]
    if not creates:
        return
    lookup = getattr(adapter, "lookup_assets_by_natural_identifier", None)
    if not callable(lookup):
        raise ImportCollisionError("import_v2 CREATE collision check is unavailable")
    seen: set[tuple[str, str]] = set()
    for action in creates:
        asset = action.desired_asset
        if asset is None:
            raise ImportCollisionError("import_v2 CREATE collision check is ambiguous")
        name = asset.name.strip()
        domain_ref = asset.domain_ref.strip()
        if not name or not domain_ref:
            raise ImportCollisionError("import_v2 CREATE collision check is ambiguous")
        key = (name, domain_ref)
        if key in seen:
            continue
        seen.add(key)
        try:
            matches = lookup(name=name, domain_ref=domain_ref)
        except ImportCollisionError:
            raise
        except Exception as exc:
            raise ImportCollisionError("import_v2 CREATE collision check failed") from exc
        if not isinstance(matches, list):
            raise ImportCollisionError("import_v2 CREATE collision check is ambiguous")
        if matches:
            raise ImportCollisionError(
                "import_v2 CREATE identifier collides with an existing Collibra asset"
            )


def _add_relation(
    command: dict[str, Any],
    *,
    relation_type_ref: str,
    direction: Literal["SOURCE", "TARGET"],
    related: dict[str, Any],
) -> None:
    relations = command.setdefault("relations", {})
    key = f"{relation_type_ref}:{direction}"
    bucket = relations.setdefault(key, [])
    bucket.append(related)


def execute_collibra_plan(
    adapter: Any,
    plan: SyncPlan,
    mapping_config: CollibraMappingConfig,
    *,
    apply: bool,
    execution_mode: str,
) -> Any:
    """Run Core REST or Import v2 for a reviewed plan. Mock always uses Core REST."""
    from governance.integrations.collibra.sync import execute_sync_plan

    mode = (execution_mode or "core_rest").strip().lower()
    if mode == "core_rest" or getattr(adapter, "mode", None) == "mock":
        return execute_sync_plan(adapter, plan, apply=apply)
    if mode != "import_v2":
        raise ImportCompileError("collibra_execution_mode must be core_rest or import_v2")
    document = compile_import_document(plan, mapping_config)
    unchanged_count = len(plan.unchanged)
    if not apply:
        return ImportExecutionResult(
            plan=plan,
            document=document,
            dry_run=True,
            submitted=False,
            submission=None,
            unchanged_count=unchanged_count,
        )
    if not document.commands:
        return ImportExecutionResult(
            plan=plan,
            document=document,
            dry_run=False,
            submitted=False,
            submission=None,
            unchanged_count=unchanged_count,
        )
    _prove_create_identifiers_absent(adapter, plan)
    submission = adapter.submit_json_import(document)
    if not isinstance(submission, ImportSubmission) or not submission.job_id:
        raise ImportCompileError("import job response missing id")
    return ImportExecutionResult(
        plan=plan,
        document=document,
        dry_run=False,
        submitted=True,
        submission=submission,
        unchanged_count=unchanged_count,
    )
