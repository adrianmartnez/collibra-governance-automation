"""Collibra integration: mapping, adapters, and safe plan-driven sync."""

from governance.integrations.collibra.adapters import (
    CollibraAdapter,
    CollibraAdapterError,
    CollibraAuthError,
    build_collibra_adapter,
)
from governance.integrations.collibra.endpoint import normalize_base_url
from governance.integrations.collibra.import_api import (
    ImportCollisionError,
    ImportCompileError,
    ImportDocument,
    ImportExecutionResult,
    ImportJobExecutionResult,
    ImportSubmission,
    compile_import_document,
    execute_collibra_plan,
    prove_import_create_identifiers_absent,
)
from governance.integrations.collibra.live import LiveCollibraAdapter
from governance.integrations.collibra.mapping import (
    CollibraMappingConfig,
    CollibraMappingError,
    load_mapping_config_file,
    map_to_desired_state,
    mapping_contains_example_placeholders,
    mock_mapping_config,
)
from governance.integrations.collibra.mock import MockCollibraAdapter, mock_remote_id
from governance.integrations.collibra.models import (
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraDesiredState,
    CollibraRelationshipSpec,
    CollibraRemoteAsset,
    CollibraRemoteAttribute,
    CollibraRemoteRelationship,
    CollibraRemoteState,
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
    SyncResult,
)
from governance.integrations.collibra.preflight import (
    format_preflight_human,
    preflight_exit_code,
    run_preflight,
)
from governance.integrations.collibra.sync import (
    PLANNER_CONTRACT_VERSION,
    build_sync_plan,
    execute_sync_plan,
)
from governance.integrations.collibra.synchronization import (
    SyncLifecycleResult,
    effective_synchronization_id,
)

__all__ = [
    "CollibraAdapter",
    "CollibraAdapterError",
    "CollibraAuthError",
    "CollibraAssetSpec",
    "CollibraAttributeSpec",
    "CollibraDesiredState",
    "CollibraMappingConfig",
    "CollibraMappingError",
    "CollibraRelationshipSpec",
    "CollibraRemoteAsset",
    "CollibraRemoteAttribute",
    "CollibraRemoteRelationship",
    "CollibraRemoteState",
    "LiveCollibraAdapter",
    "MockCollibraAdapter",
    "ImportCollisionError",
    "ImportCompileError",
    "ImportDocument",
    "ImportExecutionResult",
    "ImportJobExecutionResult",
    "ImportSubmission",
    "PLANNER_CONTRACT_VERSION",
    "SyncAction",
    "SyncActionType",
    "SyncObjectKind",
    "SyncPlan",
    "SyncLifecycleResult",
    "SyncResult",
    "build_collibra_adapter",
    "build_sync_plan",
    "compile_import_document",
    "execute_collibra_plan",
    "execute_sync_plan",
    "effective_synchronization_id",
    "prove_import_create_identifiers_absent",
    "load_mapping_config_file",
    "map_to_desired_state",
    "mapping_contains_example_placeholders",
    "mock_mapping_config",
    "mock_remote_id",
    "normalize_base_url",
    "format_preflight_human",
    "preflight_exit_code",
    "run_preflight",
]
