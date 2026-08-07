"""Collibra integration: deterministic mapping to inspectable desired state."""

from governance.integrations.collibra.mapping import (
    CollibraMappingConfig,
    CollibraMappingError,
    map_to_desired_state,
    mock_mapping_config,
)
from governance.integrations.collibra.models import (
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraDesiredState,
    CollibraRelationshipSpec,
)

__all__ = [
    "CollibraAssetSpec",
    "CollibraAttributeSpec",
    "CollibraDesiredState",
    "CollibraMappingConfig",
    "CollibraMappingError",
    "CollibraRelationshipSpec",
    "map_to_desired_state",
    "mock_mapping_config",
]
