"""In-process Collibra adapter for local deterministic demonstration.

This adapter never performs network I/O and must not be described as a live
Collibra tenant session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from governance.integrations.collibra.adapters import CollibraAdapterError
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.integrations.collibra.models import (
    CollibraAssetSpec,
    CollibraDesiredState,
    CollibraRelationshipSpec,
    CollibraRemoteAsset,
    CollibraRemoteAttribute,
    CollibraRemoteRelationship,
    CollibraRemoteState,
)

_MOCK_NAMESPACE = UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # NAMESPACE_URL
AttributeMap = dict[str, str]


def mock_remote_id(local_id: str) -> str:
    """Deterministic mock remote ID derived from a local governance ID."""
    return str(uuid.uuid5(_MOCK_NAMESPACE, f"mock-collibra:{local_id}"))


@dataclass
class _StoredAttribute:
    attribute_type_ref: str
    value: str
    remote_attribute_id: str
    managed: bool


@dataclass
class _StoredAsset:
    remote_id: str
    name: str
    display_name: str | None
    asset_type_ref: str
    domain_ref: str
    attributes: dict[str, _StoredAttribute] = field(default_factory=dict)
    local_id: str | None = None


@dataclass
class _StoredRelationship:
    remote_id: str
    source_remote_id: str
    target_remote_id: str
    relation_type_ref: str
    local_key: str | None = None


class MockCollibraAdapter:
    """Executable mock implementing the shared Collibra adapter contract."""

    def __init__(self, mapping_config: CollibraMappingConfig) -> None:
        self._config = mapping_config
        self._assets: dict[str, _StoredAsset] = {}
        self._relationships: dict[str, _StoredRelationship] = {}
        self._actions: list[dict[str, Any]] = []
        self._fail_next: str | None = None
        self._attr_seq = 0
        self._rel_seq = 0

    @property
    def mode(self) -> Literal["mock"]:
        return "mock"

    @property
    def actions(self) -> list[dict[str, Any]]:
        return list(self._actions)

    def clear_actions(self) -> None:
        self._actions.clear()

    def fail_next(self, operation: str) -> None:
        """Inject a controlled failure for the next matching operation."""
        self._fail_next = operation

    def seed_unmanaged_asset(
        self,
        *,
        remote_id: str,
        name: str,
        asset_type_ref: str | None = None,
        domain_ref: str | None = None,
        display_name: str | None = None,
        custom_attributes: AttributeMap | None = None,
    ) -> None:
        """Insert a tenant-like unmanaged asset (no managed local_id attribute)."""
        stored = _StoredAsset(
            remote_id=remote_id,
            name=name,
            display_name=display_name,
            asset_type_ref=asset_type_ref or self._config.asset_type_refs["table"],
            domain_ref=domain_ref or self._config.domain_ref,
            local_id=None,
        )
        if custom_attributes:
            for type_ref, value in custom_attributes.items():
                stored.attributes[type_ref] = _StoredAttribute(
                    attribute_type_ref=type_ref,
                    value=value,
                    remote_attribute_id=self._next_attr_id(remote_id, type_ref),
                    managed=False,
                )
        self._assets[remote_id] = stored

    def seed_unmanaged_relationship(
        self,
        *,
        remote_id: str,
        source_remote_id: str,
        target_remote_id: str,
        relation_type_ref: str,
    ) -> None:
        self._relationships[remote_id] = _StoredRelationship(
            remote_id=remote_id,
            source_remote_id=source_remote_id,
            target_remote_id=target_remote_id,
            relation_type_ref=relation_type_ref,
            local_key=None,
        )

    def seed_custom_attribute(
        self,
        remote_id: str,
        attribute_type_ref: str,
        value: str,
    ) -> None:
        """Attach a non-managed tenant attribute that updates must preserve."""
        asset = self._assets[remote_id]
        asset.attributes[attribute_type_ref] = _StoredAttribute(
            attribute_type_ref=attribute_type_ref,
            value=value,
            remote_attribute_id=self._next_attr_id(remote_id, attribute_type_ref),
            managed=False,
        )

    def read_remote_state(self, desired: CollibraDesiredState) -> CollibraRemoteState:
        del desired  # mock reads full local store; live scopes by domain/types
        self._record("read_remote_state")
        self._maybe_fail("read_remote_state")

        managed_type_refs = set(self._config.asset_type_refs.values())
        local_id_attr = self._config.attribute_type_refs["local_id"]
        managed_attr_types = set(self._config.attribute_type_refs.values())
        managed_rel_types = set(self._config.relation_type_refs.values())

        managed_assets: list[CollibraRemoteAsset] = []
        unmanaged_assets = 0
        remote_to_local: dict[str, str] = {}

        for asset in self._assets.values():
            if asset.domain_ref != self._config.domain_ref:
                unmanaged_assets += 1
                continue
            if asset.asset_type_ref not in managed_type_refs:
                unmanaged_assets += 1
                continue
            local_attr = asset.attributes.get(local_id_attr)
            if local_attr is None or not local_attr.value.strip():
                unmanaged_assets += 1
                continue
            local_id = local_attr.value
            remote_to_local[asset.remote_id] = local_id
            managed_attrs = tuple(
                CollibraRemoteAttribute(
                    attribute_type_ref=attr.attribute_type_ref,
                    value=attr.value,
                    remote_attribute_id=attr.remote_attribute_id,
                )
                for attr in sorted(
                    asset.attributes.values(),
                    key=lambda item: item.attribute_type_ref,
                )
                if attr.attribute_type_ref in managed_attr_types
            )
            managed_assets.append(
                CollibraRemoteAsset(
                    remote_id=asset.remote_id,
                    local_id=local_id,
                    name=asset.name,
                    display_name=asset.display_name,
                    asset_type_ref=asset.asset_type_ref,
                    domain_ref=asset.domain_ref,
                    managed_attributes=managed_attrs,
                )
            )

        managed_relationships: list[CollibraRemoteRelationship] = []
        unmanaged_relationships = 0
        for relationship in self._relationships.values():
            if relationship.relation_type_ref not in managed_rel_types:
                unmanaged_relationships += 1
                continue
            source_local = remote_to_local.get(relationship.source_remote_id)
            target_local = remote_to_local.get(relationship.target_remote_id)
            if source_local is None or target_local is None:
                unmanaged_relationships += 1
                continue
            managed_relationships.append(
                CollibraRemoteRelationship(
                    remote_id=relationship.remote_id,
                    source_remote_id=relationship.source_remote_id,
                    target_remote_id=relationship.target_remote_id,
                    source_local_id=source_local,
                    target_local_id=target_local,
                    relation_type_ref=relationship.relation_type_ref,
                    local_key=relationship.local_key,
                )
            )

        return CollibraRemoteState(
            assets=tuple(managed_assets),
            relationships=tuple(managed_relationships),
            unmanaged_assets_ignored=unmanaged_assets,
            unmanaged_relationships_ignored=unmanaged_relationships,
        )

    def create_asset(self, asset: CollibraAssetSpec) -> str:
        self._record("create_asset", local_id=asset.local_id, name=asset.name)
        self._maybe_fail("create_asset")
        remote_id = mock_remote_id(asset.local_id)
        if remote_id in self._assets:
            raise CollibraAdapterError(
                "mock asset already exists",
                operation="create_asset",
                endpoint_path="/mock/assets",
            )
        stored = _StoredAsset(
            remote_id=remote_id,
            name=asset.name,
            display_name=asset.display_name,
            asset_type_ref=asset.asset_type_ref,
            domain_ref=asset.domain_ref,
            local_id=asset.local_id,
        )
        for attribute in asset.attributes:
            stored.attributes[attribute.attribute_type_ref] = _StoredAttribute(
                attribute_type_ref=attribute.attribute_type_ref,
                value=attribute.value,
                remote_attribute_id=self._next_attr_id(
                    remote_id,
                    attribute.attribute_type_ref,
                ),
                managed=True,
            )
        self._assets[remote_id] = stored
        return remote_id

    def update_asset(
        self,
        remote_id: str,
        asset: CollibraAssetSpec,
        *,
        patch_name: bool = True,
        patch_display_name: bool = True,
    ) -> None:
        self._record(
            "update_asset",
            remote_id=remote_id,
            local_id=asset.local_id,
            patch_name=patch_name,
            patch_display_name=patch_display_name,
        )
        self._maybe_fail("update_asset")
        stored = self._assets.get(remote_id)
        if stored is None:
            raise CollibraAdapterError(
                "mock asset not found",
                operation="update_asset",
                endpoint_path=f"/mock/assets/{remote_id}",
            )
        if patch_name:
            stored.name = asset.name
            self._record("patch_asset_name", remote_id=remote_id)
        if patch_display_name:
            stored.display_name = asset.display_name
            self._record("patch_asset_display_name", remote_id=remote_id)
        managed_types = set(self._config.attribute_type_refs.values())
        for attribute in asset.attributes:
            if attribute.attribute_type_ref not in managed_types:
                continue
            existing = stored.attributes.get(attribute.attribute_type_ref)
            if existing is None:
                stored.attributes[attribute.attribute_type_ref] = _StoredAttribute(
                    attribute_type_ref=attribute.attribute_type_ref,
                    value=attribute.value,
                    remote_attribute_id=self._next_attr_id(
                        remote_id,
                        attribute.attribute_type_ref,
                    ),
                    managed=True,
                )
                self._record(
                    "create_attribute",
                    remote_id=remote_id,
                    attribute_type_ref=attribute.attribute_type_ref,
                )
            elif existing.value != attribute.value:
                existing.value = attribute.value
                self._record(
                    "patch_attribute",
                    remote_id=remote_id,
                    attribute_type_ref=attribute.attribute_type_ref,
                    remote_attribute_id=existing.remote_attribute_id,
                )

    def create_relationship(
        self,
        relationship: CollibraRelationshipSpec,
        *,
        source_remote_id: str,
        target_remote_id: str,
    ) -> str:
        self._record(
            "create_relationship",
            local_key=relationship.local_key,
            source_remote_id=source_remote_id,
            target_remote_id=target_remote_id,
        )
        self._maybe_fail("create_relationship")
        if source_remote_id not in self._assets or target_remote_id not in self._assets:
            raise CollibraAdapterError(
                "mock relationship endpoints missing",
                operation="create_relationship",
                endpoint_path="/mock/relations",
            )
        remote_id = f"mock-rel-{self._rel_seq}"
        self._rel_seq += 1
        self._relationships[remote_id] = _StoredRelationship(
            remote_id=remote_id,
            source_remote_id=source_remote_id,
            target_remote_id=target_remote_id,
            relation_type_ref=relationship.relation_type_ref,
            local_key=relationship.local_key,
        )
        return remote_id

    def get_stored_attribute_value(
        self,
        remote_id: str,
        attribute_type_ref: str,
    ) -> str | None:
        asset = self._assets.get(remote_id)
        if asset is None:
            return None
        attribute = asset.attributes.get(attribute_type_ref)
        return None if attribute is None else attribute.value

    def _next_attr_id(self, remote_id: str, attribute_type_ref: str) -> str:
        self._attr_seq += 1
        return f"mock-attr-{self._attr_seq}:{remote_id}:{attribute_type_ref}"

    def _record(self, operation: str, **payload: Any) -> None:
        entry = {"operation": operation, **payload}
        self._actions.append(entry)

    def _maybe_fail(self, operation: str) -> None:
        if self._fail_next == operation:
            self._fail_next = None
            raise CollibraAdapterError(
                "injected mock failure",
                operation=operation,
                endpoint_path=f"/mock/{operation}",
            )
