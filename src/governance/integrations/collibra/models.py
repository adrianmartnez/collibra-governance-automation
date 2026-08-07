"""Collibra-oriented desired and remote-state models (inspectable, no network)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def _require_non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value


@dataclass(frozen=True, slots=True)
class CollibraAttributeSpec:
    """A single attribute value destined for a Collibra asset."""

    attribute_type_ref: str
    value: str

    def __post_init__(self) -> None:
        _require_non_empty(self.attribute_type_ref, "attribute_type_ref")
        if not isinstance(self.value, str):
            raise ValueError("value must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribute_type_ref": self.attribute_type_ref,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class CollibraAssetSpec:
    """Desired Collibra asset representation derived from governance metadata.

    ``name`` is the technical full name sent as Core REST ``name``.
    ``display_name`` is the short visible name and may differ from ``name``.
    Reconciliation identity is ``local_id``, never the full name alone.
    """

    local_id: str
    name: str
    asset_type_ref: str
    domain_ref: str
    display_name: str | None = None
    attributes: tuple[CollibraAttributeSpec, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.local_id, "local_id")
        _require_non_empty(self.name, "name")
        _require_non_empty(self.asset_type_ref, "asset_type_ref")
        _require_non_empty(self.domain_ref, "domain_ref")
        if self.display_name is not None and not self.display_name.strip():
            raise ValueError("display_name must not be empty when provided")
        object.__setattr__(
            self,
            "attributes",
            tuple(sorted(self.attributes, key=lambda item: item.attribute_type_ref)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_type_ref": self.asset_type_ref,
            "attributes": [attribute.to_dict() for attribute in self.attributes],
            "display_name": self.display_name,
            "domain_ref": self.domain_ref,
            "local_id": self.local_id,
            "name": self.name,
        }


@dataclass(frozen=True, slots=True)
class CollibraRelationshipSpec:
    """Desired Collibra relationship between two local governance assets."""

    local_key: str
    source_local_id: str
    target_local_id: str
    relation_type_ref: str

    def __post_init__(self) -> None:
        _require_non_empty(self.local_key, "local_key")
        _require_non_empty(self.source_local_id, "source_local_id")
        _require_non_empty(self.target_local_id, "target_local_id")
        _require_non_empty(self.relation_type_ref, "relation_type_ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_key": self.local_key,
            "relation_type_ref": self.relation_type_ref,
            "source_local_id": self.source_local_id,
            "target_local_id": self.target_local_id,
        }


@dataclass(frozen=True, slots=True)
class CollibraDesiredState:
    """Deterministic, inspectable Collibra-oriented desired catalog state."""

    assets: tuple[CollibraAssetSpec, ...]
    relationships: tuple[CollibraRelationshipSpec, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "assets",
            tuple(sorted(self.assets, key=lambda asset: asset.local_id)),
        )
        object.__setattr__(
            self,
            "relationships",
            tuple(sorted(self.relationships, key=lambda rel: rel.local_key)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assets": [asset.to_dict() for asset in self.assets],
            "relationships": [relationship.to_dict() for relationship in self.relationships],
        }

    def to_json(self) -> str:
        return (
            json.dumps(
                self.to_dict(),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            )
            + "\n"
        )


@dataclass(frozen=True, slots=True)
class CollibraRemoteAttribute:
    """Remote attribute value for a managed attribute type."""

    attribute_type_ref: str
    value: str
    remote_attribute_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.attribute_type_ref, "attribute_type_ref")
        if not isinstance(self.value, str):
            raise ValueError("value must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribute_type_ref": self.attribute_type_ref,
            "remote_attribute_id": self.remote_attribute_id,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class CollibraRemoteAsset:
    """Managed remote asset matched by stable local_id attribute."""

    remote_id: str
    local_id: str
    name: str
    asset_type_ref: str
    domain_ref: str
    display_name: str | None = None
    managed_attributes: tuple[CollibraRemoteAttribute, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.remote_id, "remote_id")
        _require_non_empty(self.local_id, "local_id")
        _require_non_empty(self.name, "name")
        _require_non_empty(self.asset_type_ref, "asset_type_ref")
        _require_non_empty(self.domain_ref, "domain_ref")
        object.__setattr__(
            self,
            "managed_attributes",
            tuple(
                sorted(
                    self.managed_attributes,
                    key=lambda item: item.attribute_type_ref,
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_type_ref": self.asset_type_ref,
            "display_name": self.display_name,
            "domain_ref": self.domain_ref,
            "local_id": self.local_id,
            "managed_attributes": [attribute.to_dict() for attribute in self.managed_attributes],
            "name": self.name,
            "remote_id": self.remote_id,
        }


@dataclass(frozen=True, slots=True)
class CollibraRemoteRelationship:
    """Managed remote relationship between managed assets."""

    remote_id: str
    source_remote_id: str
    target_remote_id: str
    source_local_id: str
    target_local_id: str
    relation_type_ref: str
    local_key: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.remote_id, "remote_id")
        _require_non_empty(self.source_remote_id, "source_remote_id")
        _require_non_empty(self.target_remote_id, "target_remote_id")
        _require_non_empty(self.source_local_id, "source_local_id")
        _require_non_empty(self.target_local_id, "target_local_id")
        _require_non_empty(self.relation_type_ref, "relation_type_ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_key": self.local_key,
            "relation_type_ref": self.relation_type_ref,
            "remote_id": self.remote_id,
            "source_local_id": self.source_local_id,
            "source_remote_id": self.source_remote_id,
            "target_local_id": self.target_local_id,
            "target_remote_id": self.target_remote_id,
        }


@dataclass(frozen=True, slots=True)
class CollibraRemoteState:
    """Deterministic remote state containing only managed assets/relationships.

    Unmanaged tenant objects are counted as ignored and excluded from the
    assets/relationships collections used by diff/sync.
    """

    assets: tuple[CollibraRemoteAsset, ...] = ()
    relationships: tuple[CollibraRemoteRelationship, ...] = ()
    unmanaged_assets_ignored: int = 0
    unmanaged_relationships_ignored: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "assets",
            tuple(sorted(self.assets, key=lambda asset: asset.local_id)),
        )
        object.__setattr__(
            self,
            "relationships",
            tuple(
                sorted(
                    self.relationships,
                    key=lambda rel: (
                        rel.local_key or "",
                        rel.source_local_id,
                        rel.target_local_id,
                        rel.relation_type_ref,
                        rel.remote_id,
                    ),
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assets": [asset.to_dict() for asset in self.assets],
            "relationships": [relationship.to_dict() for relationship in self.relationships],
            "unmanaged_assets_ignored": self.unmanaged_assets_ignored,
            "unmanaged_relationships_ignored": self.unmanaged_relationships_ignored,
        }

    def to_json(self) -> str:
        return (
            json.dumps(
                self.to_dict(),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            )
            + "\n"
        )
