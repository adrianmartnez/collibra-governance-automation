"""Frozen Provider SDK capability identifiers."""

from __future__ import annotations

from enum import StrEnum


class CapabilityId(StrEnum):
    """Stable public capability IDs (architecture contract #89)."""

    METADATA_DISCOVERY = "metadata_discovery"
    GOVERNANCE_GRAPH = "governance_graph"
    PROPERTY_OBSERVATIONS = "property_observations"
    LINEAGE = "lineage"
    REMOTE_STATE_READ = "remote_state_read"
    TARGET_PLANNING = "target_planning"
    COMPATIBILITY_PREFLIGHT = "compatibility_preflight"
    AUTHORIZED_MUTATION = "authorized_mutation"


SUPPORTED_CAPABILITIES: frozenset[CapabilityId] = frozenset(CapabilityId)


def parse_capability_id(raw: object, *, path: str) -> CapabilityId:
    """Parse a capability ID string into CapabilityId or raise via caller."""
    from governance.providers.errors import (
        CODE_UNKNOWN_CAPABILITY,
        ProviderDescriptorError,
        ProviderDiagnostic,
    )

    if isinstance(raw, CapabilityId):
        return raw
    if not isinstance(raw, str):
        raise ProviderDescriptorError(
            [
                ProviderDiagnostic(
                    code=CODE_UNKNOWN_CAPABILITY,
                    path=path,
                    message="capability id must be a string",
                )
            ]
        )
    try:
        return CapabilityId(raw)
    except ValueError as exc:
        raise ProviderDescriptorError(
            [
                ProviderDiagnostic(
                    code=CODE_UNKNOWN_CAPABILITY,
                    path=path,
                    message=f"unknown capability id {raw!r}",
                )
            ]
        ) from exc


def sorted_capability_values(
    capabilities: frozenset[CapabilityId] | set[CapabilityId],
) -> list[str]:
    return sorted(capability.value for capability in capabilities)
