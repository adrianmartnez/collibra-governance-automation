"""Collibra integration: mapping, adapters, and safe plan-driven sync."""

from governance.integrations.collibra.adapters import (
    CollibraAdapter,
    CollibraAdapterError,
    CollibraAuthError,
    build_collibra_adapter,
)
from governance.integrations.collibra.endpoint import normalize_base_url
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
from governance.integrations.collibra.sync import (
    PLANNER_CONTRACT_VERSION,
    build_sync_plan,
    execute_sync_plan,
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
    "PLANNER_CONTRACT_VERSION",
    "SyncAction",
    "SyncActionType",
    "SyncObjectKind",
    "SyncPlan",
    "SyncResult",
    "build_collibra_adapter",
    "build_sync_plan",
    "execute_sync_plan",
    "load_mapping_config_file",
    "map_to_desired_state",
    "mapping_contains_example_placeholders",
    "mock_mapping_config",
    "mock_remote_id",
    "normalize_base_url",
]
