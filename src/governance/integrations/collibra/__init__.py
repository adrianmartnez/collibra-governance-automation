"""Collibra integration: mapping, adapters, and safe plan-driven sync."""

from governance.integrations.collibra.adapters import (
    CollibraAdapter,
    CollibraAdapterError,
    build_collibra_adapter,
)
from governance.integrations.collibra.live import LiveCollibraAdapter
from governance.integrations.collibra.mapping import (
    CollibraMappingConfig,
    CollibraMappingError,
    map_to_desired_state,
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
from governance.integrations.collibra.sync import build_sync_plan, execute_sync_plan

__all__ = [
    "CollibraAdapter",
    "CollibraAdapterError",
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
    "SyncAction",
    "SyncActionType",
    "SyncObjectKind",
    "SyncPlan",
    "SyncResult",
    "build_collibra_adapter",
    "build_sync_plan",
    "execute_sync_plan",
    "map_to_desired_state",
    "mock_mapping_config",
    "mock_remote_id",
]
