"""Plan-faithful Collibra Import API v2 compilation and json-job submit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from governance.identity.canonicalize import canonical_json_bytes
from governance.integrations.collibra.adapters import CollibraAdapterError
from governance.integrations.collibra.jobs import (
    IMPORT_SUBMISSION_UNCERTAIN,
    JobView,
    SubmissionState,
    observe_job,
)
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.integrations.collibra.models import (
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
)
from governance.integrations.collibra.sync import _ordered_mutating_actions

IMPORT_JSON_JOB_PATH = "/rest/2.0/import/json-job"

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

    Dry-run and pre-lifecycle submit inspection only. After polling completes,
    callers receive ``ImportJobExecutionResult`` instead.
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


@dataclass(frozen=True, slots=True)
class ImportJobExecutionResult:
    """Import json-job submit + bounded poll through terminal job state."""

    plan: SyncPlan
    document: ImportDocument
    dry_run: bool
    submission_state: SubmissionState
    job_id: str | None
    job: JobView | None
    success: bool
    applied_count: int
    unchanged_count: int
    import_error_summary: dict[str, int] | None = None
    error: str | None = None

    @property
    def submitted(self) -> bool | None:
        from governance.integrations.collibra.jobs import submission_state_as_bool

        return submission_state_as_bool(self.submission_state)

    @property
    def terminal(self) -> bool:
        from governance.integrations.collibra.jobs import is_remote_terminal

        return is_remote_terminal(self.job)


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

    _reject_inconsistent_asset_local_identities(plan)
    _reject_duplicate_create_natural_identifiers(plan)
    assets_by_local = _asset_lookup(plan)
    commands: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for action in _ordered_mutating_actions(mutating):
        if action.object_kind is SyncObjectKind.ASSET:
            command = _asset_command(action, allowed_attr_types=allowed_attr_types)
            local_id = _require_asset_local_identity(action)
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
        if action.object_kind is not SyncObjectKind.ASSET:
            continue
        if action.action_type in {SyncActionType.CREATE, SyncActionType.UPDATE}:
            local_id = _require_asset_local_identity(action)
        elif action.local_id:
            local_id = action.local_id
        else:
            continue
        found[local_id] = action
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
        command["identifier"] = _exact_import_asset_identifier(asset)
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


def _exact_import_asset_identifier(asset: Any) -> dict[str, Any]:
    """Import CREATE natural identifier. No trim, casefold, or other normalization."""
    if asset is None:
        raise ImportCompileError("asset action missing desired spec")
    name = asset.name
    domain_ref = asset.domain_ref
    if not isinstance(name, str) or not name.strip():
        raise ImportCompileError("import_v2 CREATE identifier is empty")
    if not isinstance(domain_ref, str) or not domain_ref.strip():
        raise ImportCompileError("import_v2 CREATE identifier is empty")
    return {
        "name": name,
        "domain": {"id": domain_ref},
    }


def _exact_import_identifier_key(asset: Any) -> tuple[str, str]:
    identifier = _exact_import_asset_identifier(asset)
    return identifier["name"], identifier["domain"]["id"]


def _require_asset_local_identity(action: SyncAction) -> str:
    """Import-only: CREATE/UPDATE asset local_id must equal desired_asset.local_id exactly."""
    if action.object_kind is not SyncObjectKind.ASSET:
        raise ImportCompileError("asset command missing local identity")
    if action.action_type not in {SyncActionType.CREATE, SyncActionType.UPDATE}:
        raise ImportCompileError("asset command missing local identity")
    if not isinstance(action.local_id, str) or action.local_id == "":
        raise ImportCompileError("import_v2 asset action missing local identity")
    asset = action.desired_asset
    if asset is None:
        raise ImportCompileError("asset action missing desired spec")
    if not isinstance(asset.local_id, str) or asset.local_id == "":
        raise ImportCompileError("import_v2 asset action missing local identity")
    if action.local_id != asset.local_id:
        raise ImportCompileError(
            "import_v2 asset action local_id must equal desired_asset.local_id"
        )
    return action.local_id


def _reject_inconsistent_asset_local_identities(plan: SyncPlan) -> None:
    for action in plan.actions:
        if action.object_kind is SyncObjectKind.ASSET and action.action_type in {
            SyncActionType.CREATE,
            SyncActionType.UPDATE,
        }:
            _require_asset_local_identity(action)


def _reject_duplicate_create_natural_identifiers(plan: SyncPlan) -> None:
    """Fail closed when two CREATE local identities share one Import MERGE key."""
    seen: dict[tuple[str, str], str] = {}
    for action in plan.actions:
        if (
            action.object_kind is not SyncObjectKind.ASSET
            or action.action_type is not SyncActionType.CREATE
        ):
            continue
        key = _exact_import_identifier_key(action.desired_asset)
        local_id = _require_asset_local_identity(action)
        previous = seen.get(key)
        if previous is not None and previous != local_id:
            raise ImportCompileError(
                "import_v2 cannot represent two CREATE assets with the same name and domain"
            )
        seen[key] = local_id


def _asset_identifier(action: SyncAction) -> dict[str, Any]:
    if action.action_type is SyncActionType.CREATE:
        return _exact_import_asset_identifier(action.desired_asset)
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


def prove_import_create_identifiers_absent(adapter: Any, plan: SyncPlan) -> None:
    """Fail closed unless each CREATE name+domain is observably absent.

    Shared by import_v2 and sync_v2 live execution. Import MERGE identifies
    CREATE by name+domain, not managed ``local_id``. Compiler stays offline.
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
        try:
            name, domain_ref = _exact_import_identifier_key(action.desired_asset)
        except ImportCompileError as exc:
            raise ImportCollisionError("import_v2 CREATE collision check is ambiguous") from exc
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


def _best_effort_import_error_summary(adapter: Any, job_id: str) -> dict[str, int] | None:
    if not job_id:
        return None
    get_errors = getattr(adapter, "get_import_errors", None)
    if not callable(get_errors):
        return None
    try:
        summary = get_errors(job_id)
    except Exception:
        return None
    if isinstance(summary, dict) and "error_count" in summary:
        return {"error_count": int(summary["error_count"])}
    return None


def _execute_import_batches_lifecycle(
    adapter: Any,
    plan: SyncPlan,
    document: ImportDocument,
    batches: tuple[ImportDocument, ...],
    *,
    unchanged_count: int,
) -> ImportJobExecutionResult:
    from governance.integrations.collibra.jobs import is_terminal_success

    prove_import_create_identifiers_absent(adapter, plan)
    poll_job = getattr(adapter, "poll_job", None)
    if poll_job is None:
        return ImportJobExecutionResult(
            plan=plan,
            document=document,
            dry_run=False,
            submission_state="not_attempted",
            job_id=None,
            job=None,
            success=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error="import_v2 requires job polling",
        )

    applied = 0
    last_job_id: str | None = None
    last_view: JobView | None = None
    for batch in batches:
        try:
            submission = adapter.submit_json_import(batch)
        except CollibraAdapterError:
            return ImportJobExecutionResult(
                plan=plan,
                document=document,
                dry_run=False,
                submission_state="unknown",
                job_id=last_job_id,
                job=last_view,
                success=False,
                applied_count=applied,
                unchanged_count=unchanged_count,
                error=IMPORT_SUBMISSION_UNCERTAIN,
            )
        job_id = getattr(submission, "job_id", "") or ""
        if not job_id:
            return ImportJobExecutionResult(
                plan=plan,
                document=document,
                dry_run=False,
                submission_state="unknown",
                job_id=last_job_id,
                job=last_view,
                success=False,
                applied_count=applied,
                unchanged_count=unchanged_count,
                error=IMPORT_SUBMISSION_UNCERTAIN,
            )
        view, observation_error = observe_job(poll_job, job_id)
        if observation_error is not None:
            return ImportJobExecutionResult(
                plan=plan,
                document=document,
                dry_run=False,
                submission_state="submitted",
                job_id=job_id,
                job=None,
                success=False,
                applied_count=applied,
                unchanged_count=unchanged_count,
                error=observation_error,
            )
        assert view is not None
        last_job_id = job_id
        last_view = view
        if not is_terminal_success(view):
            outcome = view.normalized_outcome or view.normalized_state
            return ImportJobExecutionResult(
                plan=plan,
                document=document,
                dry_run=False,
                submission_state="submitted",
                job_id=job_id,
                job=view,
                success=False,
                applied_count=applied,
                unchanged_count=unchanged_count,
                import_error_summary=_best_effort_import_error_summary(adapter, job_id),
                error=f"import job outcome={outcome}",
            )
        applied += len(batch.commands)

    return ImportJobExecutionResult(
        plan=plan,
        document=document,
        dry_run=False,
        submission_state="submitted",
        job_id=last_job_id,
        job=last_view,
        success=True,
        applied_count=applied,
        unchanged_count=unchanged_count,
    )


def _execute_import_job_lifecycle(
    adapter: Any,
    plan: SyncPlan,
    document: ImportDocument,
    *,
    unchanged_count: int,
) -> ImportJobExecutionResult:
    return _execute_import_batches_lifecycle(
        adapter,
        plan,
        document,
        (document,),
        unchanged_count=unchanged_count,
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
    synchronization_id: str | None = None,
    max_resources: int | None = None,
    max_additional_characteristics: int | None = None,
) -> Any:
    """Run Core REST, Import v2, or sync_v2. Mock always uses Core REST."""
    from governance.integrations.collibra.batching import partition_document
    from governance.integrations.collibra.sync import execute_sync_plan
    from governance.integrations.collibra.synchronization import execute_sync_v2

    mode = (execution_mode or "core_rest").strip().lower()
    if mode == "core_rest" or getattr(adapter, "mode", None) == "mock":
        return execute_sync_plan(adapter, plan, apply=apply)
    if mode == "sync_v2":
        if not synchronization_id:
            raise ImportCompileError("sync_v2 requires a synchronization id")
        return execute_sync_v2(
            adapter,
            plan,
            mapping_config,
            apply=apply,
            synchronization_id=synchronization_id,
            max_resources=max_resources,
            max_additional_characteristics=max_additional_characteristics,
        )
    if mode != "import_v2":
        raise ImportCompileError("collibra_execution_mode must be core_rest, import_v2, or sync_v2")
    document = compile_import_document(plan, mapping_config)
    unchanged_count = len(plan.unchanged)
    batches = partition_document(
        document,
        max_resources=max_resources,
        max_additional_characteristics=max_additional_characteristics,
    )
    if not apply:
        return ImportExecutionResult(
            plan=plan,
            document=document,
            dry_run=True,
            submitted=False,
            submission=None,
            unchanged_count=unchanged_count,
        )
    if not batches:
        return ImportJobExecutionResult(
            plan=plan,
            document=document,
            dry_run=False,
            submission_state="not_attempted",
            job_id=None,
            job=None,
            success=True,
            applied_count=0,
            unchanged_count=unchanged_count,
        )
    return _execute_import_batches_lifecycle(
        adapter,
        plan,
        document,
        batches,
        unchanged_count=unchanged_count,
    )
