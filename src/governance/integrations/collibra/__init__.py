"""Collibra integration: mapping and mock/live adapter boundaries."""

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
)

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
    "build_collibra_adapter",
    "map_to_desired_state",
    "mock_mapping_config",
    "mock_remote_id",
]
