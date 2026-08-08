"""Deterministic Collibra mapping from vendor-neutral governance metadata."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from governance.domain import (
    Column,
    Database,
    GovernanceModel,
    Ownership,
    Table,
)
from governance.exporters import MetadataInventory
from governance.integrations.collibra.models import (
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraDesiredState,
    CollibraRelationshipSpec,
)

ASSET_TYPE_KEYS = ("database", "schema", "table", "column")
RELATION_TYPE_KEYS = ("database_schema", "schema_table", "table_column", "table_fk")
ATTRIBUTE_TYPE_KEYS = (
    "local_id",
    "description",
    "owner",
    "data_type",
    "nullable",
    "ordinal_position",
)


class CollibraMappingError(ValueError):
    """Raised when Collibra mapping configuration or input is invalid."""


@dataclass(frozen=True, slots=True)
class CollibraMappingConfig:
    """Tenant-specific Collibra type and domain references.

    Values are configuration-driven. Mock mode uses symbolic ``mock:*`` refs that
    are local test identifiers, not commercial tenant UUIDs.
    """

    domain_ref: str
    asset_type_refs: Mapping[str, str]
    relation_type_refs: Mapping[str, str]
    attribute_type_refs: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.domain_ref, str) or not self.domain_ref.strip():
            raise CollibraMappingError("domain_ref is required")
        object.__setattr__(
            self,
            "asset_type_refs",
            _validate_ref_map(self.asset_type_refs, ASSET_TYPE_KEYS, "asset_type_refs"),
        )
        object.__setattr__(
            self,
            "relation_type_refs",
            _validate_ref_map(
                self.relation_type_refs,
                RELATION_TYPE_KEYS,
                "relation_type_refs",
            ),
        )
        object.__setattr__(
            self,
            "attribute_type_refs",
            _validate_ref_map(
                self.attribute_type_refs,
                ATTRIBUTE_TYPE_KEYS,
                "attribute_type_refs",
            ),
        )

    def to_identity_dict(self) -> dict[str, Any]:
        """Normalized mapping content for content identity (path-independent)."""
        return {
            "attribute_type_refs": dict(sorted(self.attribute_type_refs.items())),
            "asset_type_refs": dict(sorted(self.asset_type_refs.items())),
            "domain_ref": self.domain_ref,
            "relation_type_refs": dict(sorted(self.relation_type_refs.items())),
        }


def _validate_ref_map(
    refs: Mapping[str, str],
    required_keys: tuple[str, ...],
    field_name: str,
) -> dict[str, str]:
    if not isinstance(refs, Mapping):
        raise CollibraMappingError(f"{field_name} must be a mapping")
    missing = [key for key in required_keys if key not in refs]
    if missing:
        raise CollibraMappingError(f"{field_name} missing required keys: {', '.join(missing)}")
    validated: dict[str, str] = {}
    for key in required_keys:
        value = refs[key]
        if not isinstance(value, str) or not value.strip():
            raise CollibraMappingError(f"{field_name}[{key!r}] must be a non-empty string")
        validated[key] = value
    return validated


def mock_mapping_config() -> CollibraMappingConfig:
    """Return mapping refs clearly identified as local mock symbols."""
    return CollibraMappingConfig(
        domain_ref="mock:domain:governance",
        asset_type_refs={
            "database": "mock:asset-type:database",
            "schema": "mock:asset-type:schema",
            "table": "mock:asset-type:table",
            "column": "mock:asset-type:column",
        },
        relation_type_refs={
            "database_schema": "mock:relation-type:database-schema",
            "schema_table": "mock:relation-type:schema-table",
            "table_column": "mock:relation-type:table-column",
            "table_fk": "mock:relation-type:table-fk",
        },
        attribute_type_refs={
            "local_id": "mock:attribute-type:local-id",
            "description": "mock:attribute-type:description",
            "owner": "mock:attribute-type:owner",
            "data_type": "mock:attribute-type:data-type",
            "nullable": "mock:attribute-type:nullable",
            "ordinal_position": "mock:attribute-type:ordinal-position",
        },
    )


_EXAMPLE_PLACEHOLDER_RE = re.compile(r"<[^>]+>")


def load_mapping_config_file(path: str | Path) -> CollibraMappingConfig:
    """Load a UTF-8 JSON mapping config file into CollibraMappingConfig.

    Errors omit file contents and credentials. Auth never belongs in this file.
    """
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise CollibraMappingError("invalid Collibra mapping configuration") from exc

    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CollibraMappingError("invalid Collibra mapping configuration") from exc

    if not isinstance(payload, dict):
        raise CollibraMappingError("invalid Collibra mapping configuration")

    try:
        domain_ref = payload["domain_ref"]
        asset_type_refs = payload["asset_type_refs"]
        relation_type_refs = payload["relation_type_refs"]
        attribute_type_refs = payload["attribute_type_refs"]
    except KeyError as exc:
        raise CollibraMappingError("invalid Collibra mapping configuration") from exc

    try:
        return CollibraMappingConfig(
            domain_ref=domain_ref,
            asset_type_refs=asset_type_refs,
            relation_type_refs=relation_type_refs,
            attribute_type_refs=attribute_type_refs,
        )
    except CollibraMappingError:
        raise
    except (TypeError, ValueError) as exc:
        raise CollibraMappingError("invalid Collibra mapping configuration") from exc


def mapping_contains_example_placeholders(config: CollibraMappingConfig) -> bool:
    """Return True when any ref still looks like a sample ``<placeholder>``."""
    values = [config.domain_ref]
    values.extend(str(value) for value in config.asset_type_refs.values())
    values.extend(str(value) for value in config.relation_type_refs.values())
    values.extend(str(value) for value in config.attribute_type_refs.values())
    return any(_EXAMPLE_PLACEHOLDER_RE.search(value) for value in values)


def map_to_desired_state(
    source: GovernanceModel | MetadataInventory,
    config: CollibraMappingConfig,
) -> CollibraDesiredState:
    """Map governance metadata into an inspectable Collibra desired state.

    Performs no network I/O and does not query PostgreSQL.
    """
    model = source.model if isinstance(source, MetadataInventory) else source
    if not isinstance(model, GovernanceModel):
        raise CollibraMappingError("source must be a GovernanceModel or MetadataInventory")

    assets: list[CollibraAssetSpec] = []
    relationships: list[CollibraRelationshipSpec] = []
    asset_ids: set[str] = set()

    for data_source in model.data_sources:
        for database in data_source.databases:
            _map_database(
                database=database,
                config=config,
                assets=assets,
                relationships=relationships,
                asset_ids=asset_ids,
            )

    for relationship in model.relationships:
        if relationship.from_table_id not in asset_ids:
            raise CollibraMappingError(
                f"relationship source asset missing: {relationship.from_table_id}"
            )
        if relationship.to_table_id not in asset_ids:
            raise CollibraMappingError(
                f"relationship target asset missing: {relationship.to_table_id}"
            )
        relationships.append(
            CollibraRelationshipSpec(
                local_key=relationship.id,
                source_local_id=relationship.from_table_id,
                target_local_id=relationship.to_table_id,
                relation_type_ref=config.relation_type_refs["table_fk"],
            )
        )

    _assert_unique_relationship_keys(relationships)
    return CollibraDesiredState(assets=tuple(assets), relationships=tuple(relationships))


def _map_database(
    *,
    database: Database,
    config: CollibraMappingConfig,
    assets: list[CollibraAssetSpec],
    relationships: list[CollibraRelationshipSpec],
    asset_ids: set[str],
) -> None:
    _add_asset(
        assets,
        asset_ids,
        CollibraAssetSpec(
            local_id=database.id,
            name=database.name,
            display_name=database.name,
            asset_type_ref=config.asset_type_refs["database"],
            domain_ref=config.domain_ref,
            attributes=_common_attributes(
                config=config,
                local_id=database.id,
                description=database.description,
                ownership=database.ownership,
            ),
        ),
    )
    for schema in database.schemas:
        schema_full_name = f"{database.name}.{schema.name}"
        _add_asset(
            assets,
            asset_ids,
            CollibraAssetSpec(
                local_id=schema.id,
                name=schema_full_name,
                display_name=schema.name,
                asset_type_ref=config.asset_type_refs["schema"],
                domain_ref=config.domain_ref,
                attributes=_common_attributes(
                    config=config,
                    local_id=schema.id,
                    description=schema.description,
                    ownership=schema.ownership,
                ),
            ),
        )
        relationships.append(
            CollibraRelationshipSpec(
                local_key=f"rel:contains:{database.id}->{schema.id}",
                source_local_id=database.id,
                target_local_id=schema.id,
                relation_type_ref=config.relation_type_refs["database_schema"],
            )
        )
        for table in schema.tables:
            table_full_name = f"{database.name}.{schema.name}.{table.name}"
            _add_asset(
                assets,
                asset_ids,
                CollibraAssetSpec(
                    local_id=table.id,
                    name=table_full_name,
                    display_name=table.name,
                    asset_type_ref=config.asset_type_refs["table"],
                    domain_ref=config.domain_ref,
                    attributes=_common_attributes(
                        config=config,
                        local_id=table.id,
                        description=table.description,
                        ownership=table.ownership,
                    ),
                ),
            )
            relationships.append(
                CollibraRelationshipSpec(
                    local_key=f"rel:contains:{schema.id}->{table.id}",
                    source_local_id=schema.id,
                    target_local_id=table.id,
                    relation_type_ref=config.relation_type_refs["schema_table"],
                )
            )
            for column in table.columns:
                _map_column(
                    database_name=database.name,
                    schema_name=schema.name,
                    table=table,
                    column=column,
                    config=config,
                    assets=assets,
                    relationships=relationships,
                    asset_ids=asset_ids,
                )


def _map_column(
    *,
    database_name: str,
    schema_name: str,
    table: Table,
    column: Column,
    config: CollibraMappingConfig,
    assets: list[CollibraAssetSpec],
    relationships: list[CollibraRelationshipSpec],
    asset_ids: set[str],
) -> None:
    column_full_name = f"{database_name}.{schema_name}.{table.name}.{column.name}"
    attributes = list(
        _common_attributes(
            config=config,
            local_id=column.id,
            description=column.description,
            ownership=None,
        )
    )
    attributes.extend(
        [
            CollibraAttributeSpec(
                attribute_type_ref=config.attribute_type_refs["data_type"],
                value=column.data_type,
            ),
            CollibraAttributeSpec(
                attribute_type_ref=config.attribute_type_refs["nullable"],
                value="true" if column.nullable else "false",
            ),
            CollibraAttributeSpec(
                attribute_type_ref=config.attribute_type_refs["ordinal_position"],
                value=str(column.ordinal_position),
            ),
        ]
    )
    _add_asset(
        assets,
        asset_ids,
        CollibraAssetSpec(
            local_id=column.id,
            name=column_full_name,
            display_name=column.name,
            asset_type_ref=config.asset_type_refs["column"],
            domain_ref=config.domain_ref,
            attributes=tuple(attributes),
        ),
    )
    relationships.append(
        CollibraRelationshipSpec(
            local_key=f"rel:contains:{table.id}->{column.id}",
            source_local_id=table.id,
            target_local_id=column.id,
            relation_type_ref=config.relation_type_refs["table_column"],
        )
    )


def _common_attributes(
    *,
    config: CollibraMappingConfig,
    local_id: str,
    description: str | None,
    ownership: Ownership | None,
) -> tuple[CollibraAttributeSpec, ...]:
    attributes: list[CollibraAttributeSpec] = [
        CollibraAttributeSpec(
            attribute_type_ref=config.attribute_type_refs["local_id"],
            value=local_id,
        )
    ]
    if description is not None and description.strip():
        attributes.append(
            CollibraAttributeSpec(
                attribute_type_ref=config.attribute_type_refs["description"],
                value=description,
            )
        )
    if ownership is not None:
        attributes.append(
            CollibraAttributeSpec(
                attribute_type_ref=config.attribute_type_refs["owner"],
                value=ownership.owner_name,
            )
        )
    return tuple(attributes)


def _add_asset(
    assets: list[CollibraAssetSpec],
    asset_ids: set[str],
    asset: CollibraAssetSpec,
) -> None:
    if asset.local_id in asset_ids:
        raise CollibraMappingError(f"duplicate local asset ID: {asset.local_id}")
    asset_ids.add(asset.local_id)
    assets.append(asset)


def _assert_unique_relationship_keys(relationships: list[CollibraRelationshipSpec]) -> None:
    seen: set[str] = set()
    for relationship in relationships:
        if relationship.local_key in seen:
            raise CollibraMappingError(f"duplicate relationship key: {relationship.local_key}")
        seen.add(relationship.local_key)
