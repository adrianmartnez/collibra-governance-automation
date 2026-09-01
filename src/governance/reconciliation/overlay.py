"""Typed remote-aware reconciliation overlay onto Collibra desired state."""

from __future__ import annotations

from dataclasses import dataclass

from governance.domain.conflicts import PropertyConflictReport, PropertyConflictResult
from governance.domain.observations import PropertyPath
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.integrations.collibra.models import (
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraDesiredState,
    CollibraRemoteAsset,
    CollibraRemoteState,
)
from governance.reconciliation.physical_index import PhysicalReconciliationIndex
from governance.reconciliation.safety import SAFE_STATES, assess_reconciliation
from governance.reconciliation.targets import (
    PATH_DATA_TYPE,
    PATH_DESCRIPTION,
    PATH_NAME,
    PATH_OWNERSHIP,
    RepresentableTargetValue,
    convert_effective_value,
    path_applicable_to_identity,
    target_field_for_path,
)


@dataclass(frozen=True, slots=True)
class OverlayResult:
    desired: CollibraDesiredState
    applied_results: tuple[PropertyConflictResult, ...]


def _remote_by_local_id(remote: CollibraRemoteState) -> dict[str, CollibraRemoteAsset]:
    return {asset.local_id: asset for asset in remote.assets if asset.local_id}


def _attr_map(attrs: tuple[CollibraAttributeSpec, ...]) -> dict[str, str]:
    return {item.attribute_type_ref: item.value for item in attrs}


def _remote_attr_map(asset: CollibraRemoteAsset) -> dict[str, str]:
    return {item.attribute_type_ref: item.value for item in asset.managed_attributes}


def _set_or_remove_attr(
    attrs: dict[str, str],
    *,
    type_ref: str,
    has_value: bool,
    value: str | None,
) -> dict[str, str]:
    updated = dict(attrs)
    if has_value and value is not None:
        updated[type_ref] = value
    else:
        updated.pop(type_ref, None)
    return updated


def _attrs_tuple(attrs: dict[str, str]) -> tuple[CollibraAttributeSpec, ...]:
    return tuple(
        CollibraAttributeSpec(attribute_type_ref=ref, value=value)
        for ref, value in sorted(attrs.items())
    )


def _apply_target_to_asset(
    asset: CollibraAssetSpec,
    *,
    converted: RepresentableTargetValue,
    mapping_config: CollibraMappingConfig,
    remote_asset: CollibraRemoteAsset | None,
) -> CollibraAssetSpec:
    """Apply representable target; null is CREATE-omit / existing PRESERVE REMOTE."""
    attrs = _attr_map(asset.attributes)
    display_name = asset.display_name

    if converted.field == "display_name":
        if converted.has_value:
            display_name = converted.value
        elif remote_asset is None:
            display_name = None
        else:
            display_name = remote_asset.display_name
        return CollibraAssetSpec(
            local_id=asset.local_id,
            name=asset.name,
            asset_type_ref=asset.asset_type_ref,
            domain_ref=asset.domain_ref,
            display_name=display_name,
            attributes=asset.attributes,
        )

    type_key = {
        "description": "description",
        "data_type": "data_type",
        "owner": "owner",
    }[converted.field]
    type_ref = mapping_config.attribute_type_refs[type_key]

    if converted.has_value:
        attrs = _set_or_remove_attr(
            attrs, type_ref=type_ref, has_value=True, value=converted.value
        )
    elif remote_asset is None:
        attrs = _set_or_remove_attr(attrs, type_ref=type_ref, has_value=False, value=None)
    else:
        remote_attrs = _remote_attr_map(remote_asset)
        if type_ref in remote_attrs:
            attrs[type_ref] = remote_attrs[type_ref]
        else:
            attrs.pop(type_ref, None)

    return CollibraAssetSpec(
        local_id=asset.local_id,
        name=asset.name,
        asset_type_ref=asset.asset_type_ref,
        domain_ref=asset.domain_ref,
        display_name=display_name,
        attributes=_attrs_tuple(attrs),
    )


def apply_reconciliation_overlay(
    baseline_desired: CollibraDesiredState,
    remote_state: CollibraRemoteState,
    conflict_report: PropertyConflictReport,
    mapping_config: CollibraMappingConfig,
    physical_index: PhysicalReconciliationIndex,
) -> OverlayResult:
    """Overlay safe+representable effective values; leave baseline for unsafe/unrepresentable."""
    remote_map = _remote_by_local_id(remote_state)
    assets_by_id = {asset.local_id: asset for asset in baseline_desired.assets}
    applied: list[PropertyConflictResult] = []

    results_by_key: dict[tuple[bytes, str], PropertyConflictResult] = {
        (item.object_identity.canonical_bytes(), item.property_path.to_pointer()): item
        for item in conflict_report.results
    }

    # Only consider results whose object projects into the physical index.
    for local_id, identity in physical_index.by_local_id.items():
        asset = assets_by_id.get(local_id)
        if asset is None:
            continue
        remote_asset = remote_map.get(local_id)
        for path in (PATH_NAME, PATH_DESCRIPTION, PATH_DATA_TYPE, PATH_OWNERSHIP):
            if not path_applicable_to_identity(path, identity):
                continue
            result = results_by_key.get((identity.canonical_bytes(), path.to_pointer()))
            if result is None:
                continue
            assessment = assess_reconciliation(result)
            if not assessment.applicable or not assessment.safe:
                # Retain decision for later relevance; do not modify baseline.
                continue
            if result.state not in SAFE_STATES or not result.has_effective_value:
                continue
            converted = convert_effective_value(
                path=path,
                identity=identity,
                value=result.effective_value,
                has_effective_value=True,
            )
            if converted is None:
                continue
            assets_by_id[local_id] = _apply_target_to_asset(
                assets_by_id[local_id],
                converted=converted,
                mapping_config=mapping_config,
                remote_asset=remote_asset,
            )
            applied.append(result)

    ordered_assets = tuple(
        assets_by_id[asset.local_id] for asset in baseline_desired.assets
    )
    return OverlayResult(
        desired=CollibraDesiredState(
            assets=ordered_assets,
            relationships=baseline_desired.relationships,
        ),
        applied_results=tuple(applied),
    )


def get_asset_attr(
    asset: CollibraAssetSpec,
    *,
    mapping_config: CollibraMappingConfig,
    field: str,
) -> str | None:
    if field == "display_name":
        return asset.display_name
    type_ref = mapping_config.attribute_type_refs[field]
    for attr in asset.attributes:
        if attr.attribute_type_ref == type_ref:
            return attr.value
    return None


def get_remote_attr(
    remote: CollibraRemoteAsset | None,
    *,
    mapping_config: CollibraMappingConfig,
    field: str,
) -> str | None:
    if remote is None:
        return None
    if field == "display_name":
        return remote.display_name
    type_ref = mapping_config.attribute_type_refs[field]
    for attr in remote.managed_attributes:
        if attr.attribute_type_ref == type_ref:
            return attr.value
    return None


def field_for_path(path: PropertyPath) -> str | None:
    return target_field_for_path(path)
