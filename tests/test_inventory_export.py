"""Unit tests for deterministic metadata inventory export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.domain import (
    Column,
    Database,
    DataSource,
    ForeignKey,
    GovernanceModel,
    PrimaryKey,
    Relationship,
    Schema,
    Table,
    make_column_id,
    make_database_id,
    make_datasource_id,
    make_foreign_key_id,
    make_primary_key_id,
    make_relationship_id,
    make_schema_id,
    make_table_id,
)
from governance.exporters import (
    INVENTORY_SCHEMA,
    INVENTORY_VERSION,
    SCANNER_CONTRACT_VERSION,
    InventoryExportError,
    MetadataInventory,
    write_inventory,
)


def _sample_model(*, system_type: str = "postgresql") -> GovernanceModel:
    source_name = "governance-demo"
    database_name = "governance_demo"
    schema_name = "sales"
    table_name = "orders"
    column_name = "id"

    datasource_id = make_datasource_id(source_name)
    database_id = make_database_id(source_name, database_name)
    schema_id = make_schema_id(source_name, database_name, schema_name)
    table_id = make_table_id(source_name, database_name, schema_name, table_name)
    column_id = make_column_id(source_name, database_name, schema_name, table_name, column_name)
    pk_id = make_primary_key_id(table_id, "orders_pkey")
    fk_id = make_foreign_key_id(table_id, "orders_self_fkey")

    column = Column(
        id=column_id,
        name=column_name,
        data_type="integer",
        ordinal_position=1,
        nullable=False,
    )
    foreign_key = ForeignKey(
        id=fk_id,
        name="orders_self_fkey",
        table_id=table_id,
        column_ids=(column_id,),
        referenced_table_id=table_id,
        referenced_column_ids=(column_id,),
    )
    table = Table(
        id=table_id,
        name=table_name,
        schema_id=schema_id,
        columns=(column,),
        primary_key=PrimaryKey(
            id=pk_id,
            name="orders_pkey",
            table_id=table_id,
            column_ids=(column_id,),
        ),
        foreign_keys=(foreign_key,),
    )
    schema = Schema(
        id=schema_id,
        name=schema_name,
        database_id=database_id,
        tables=(table,),
    )
    database = Database(
        id=database_id,
        name=database_name,
        datasource_id=datasource_id,
        schemas=(schema,),
    )
    data_source = DataSource(
        id=datasource_id,
        name=source_name,
        system_type=system_type,
        databases=(database,),
    )
    relationship = Relationship(
        id=make_relationship_id(fk_id),
        name=foreign_key.name,
        from_table_id=table_id,
        to_table_id=table_id,
        foreign_key_id=fk_id,
    )
    return GovernanceModel(data_sources=(data_source,), relationships=(relationship,))


def test_inventory_constants_and_root_contract() -> None:
    model = _sample_model()
    inventory = MetadataInventory.from_model(model)
    payload = inventory.to_dict()
    assert INVENTORY_SCHEMA == "governance-metadata-inventory"
    assert INVENTORY_VERSION == "1.0"
    assert SCANNER_CONTRACT_VERSION == "1"
    assert set(payload.keys()) == {
        "inventory_schema",
        "inventory_version",
        "source",
        "scan",
        "governance",
    }
    assert payload["inventory_schema"] == INVENTORY_SCHEMA
    assert payload["inventory_version"] == INVENTORY_VERSION
    assert payload["source"] == {
        "database": "governance_demo",
        "name": "governance-demo",
        "system_type": "postgresql",
    }
    assert payload["scan"] == {
        "scanner": "postgresql",
        "scanner_contract_version": "1",
    }
    assert payload["governance"] == model.to_dict()


def test_from_model_validation() -> None:
    model = _sample_model()
    inventory = MetadataInventory.from_model(model)
    assert inventory.source_name == "governance-demo"
    assert inventory.database_name == "governance_demo"
    assert inventory.system_type == "postgresql"

    multi_source = GovernanceModel(
        data_sources=(
            model.data_sources[0],
            DataSource(
                id="ds:other",
                name="other",
                system_type="postgresql",
                databases=model.data_sources[0].databases,
            ),
        )
    )
    with pytest.raises(ValueError, match="exactly one DataSource"):
        MetadataInventory.from_model(multi_source)

    empty_db_source = DataSource(
        id="ds:empty",
        name="empty",
        system_type="postgresql",
        databases=(),
    )
    with pytest.raises(ValueError, match="exactly one Database"):
        MetadataInventory.from_model(GovernanceModel(data_sources=(empty_db_source,)))

    with pytest.raises(ValueError, match="postgresql"):
        MetadataInventory.from_model(_sample_model(system_type="mysql"))


def test_scanner_fields_not_user_overridable() -> None:
    model = _sample_model()
    with pytest.raises(TypeError):
        MetadataInventory(
            model=model,
            source_name="governance-demo",
            database_name="governance_demo",
            scanner="mysql",  # type: ignore[call-arg]
        )
    inventory = MetadataInventory.from_model(model)
    assert inventory.scanner == "postgresql"
    assert inventory.scanner_contract_version == "1"


def test_to_json_deterministic_and_human_readable() -> None:
    inventory = MetadataInventory.from_model(_sample_model())
    text = inventory.to_json()
    assert text.endswith("\n")
    assert "\n  " in text
    assert json.loads(text)["inventory_schema"] == INVENTORY_SCHEMA
    assert inventory.to_json() == text
    assert "generated_at" not in text
    assert "password" not in text.lower()
    assert inventory.to_dict()["governance"]["relationships"]


def test_write_inventory_creates_parents_and_content(tmp_path: Path) -> None:
    inventory = MetadataInventory.from_model(_sample_model())
    target = tmp_path / "nested" / "out" / "inventory.json"
    written = write_inventory(inventory, target)
    assert written == target
    assert target.read_text(encoding="utf-8") == inventory.to_json()


def test_write_inventory_rejects_directory_target(tmp_path: Path) -> None:
    inventory = MetadataInventory.from_model(_sample_model())
    with pytest.raises(InventoryExportError, match="directory"):
        write_inventory(inventory, tmp_path)
