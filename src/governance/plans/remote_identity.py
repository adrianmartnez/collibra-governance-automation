"""Managed remote-state identity projection (excludes unmanaged counts)."""

from __future__ import annotations

from typing import Any

from governance.integrations.collibra.models import CollibraRemoteState


def remote_state_identity_projection(remote: CollibraRemoteState) -> dict[str, Any]:
    assets = [
        {
            "asset_type_ref": asset.asset_type_ref,
            "display_name": asset.display_name,
            "domain_ref": asset.domain_ref,
            "local_id": asset.local_id,
            "managed_attributes": [
                {
                    "attribute_type_ref": attr.attribute_type_ref,
                    "value": attr.value,
                }
                for attr in asset.managed_attributes
            ],
            "name": asset.name,
            "remote_id": asset.remote_id,
        }
        for asset in sorted(remote.assets, key=lambda item: item.local_id)
    ]
    relationships = [
        {
            "local_key": rel.local_key,
            "relation_type_ref": rel.relation_type_ref,
            "remote_id": rel.remote_id,
            "source_local_id": rel.source_local_id,
            "source_remote_id": rel.source_remote_id,
            "target_local_id": rel.target_local_id,
            "target_remote_id": rel.target_remote_id,
        }
        for rel in sorted(
            remote.relationships,
            key=lambda item: (
                item.local_key or "",
                item.source_local_id,
                item.target_local_id,
                item.relation_type_ref,
                item.remote_id,
            ),
        )
    ]
    return {"assets": assets, "relationships": relationships}
