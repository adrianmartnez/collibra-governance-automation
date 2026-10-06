"""Provider-neutral governance.yaml v2 canonical models and load metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from governance.config_contract.models import ArtifactsConfig, AuthorityConfig, PoliciesConfig


@dataclass(frozen=True, slots=True)
class ProviderBoundConfig:
    """One source or target entry with unresolved provider-specific config."""

    id: str
    provider_id: str
    config: Mapping[str, object]

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": dict(self.config),
            "id": self.id,
            "provider": self.provider_id,
        }


@dataclass(frozen=True, slots=True)
class CanonicalConfigV2:
    """Normalized v2 configuration (no secrets, no absolute artifact paths)."""

    schema_version: str
    sources: tuple[ProviderBoundConfig, ...]
    targets: tuple[ProviderBoundConfig, ...]
    artifacts: ArtifactsConfig
    policies: PoliciesConfig
    config_root: str
    authority: AuthorityConfig = AuthorityConfig()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "artifacts": self.artifacts.to_dict(),
            "policies": self.policies.to_dict(),
            "schema_version": self.schema_version,
            "sources": [source.to_dict() for source in self.sources],
        }
        if self.targets:
            payload["targets"] = [target.to_dict() for target in self.targets]
        if self.authority.files:
            payload["authority"] = self.authority.to_dict()
        return payload

    def identity_projection(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "policies": self.policies.to_dict(),
            "schema_version": self.schema_version,
            "sources": [source.to_dict() for source in self.sources],
        }
        if self.targets:
            payload["targets"] = [target.to_dict() for target in self.targets]
        if self.authority.files:
            payload["authority"] = self.authority.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class EffectiveProviderLocations:
    """JSON Pointer bases for post-profile effective document indices."""

    source_locations: Mapping[str, str]
    target_locations: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class LoadedConfigV2:
    """Atomic v2 load result: canonical semantics + diagnostic locations."""

    canonical: CanonicalConfigV2
    locations: EffectiveProviderLocations
