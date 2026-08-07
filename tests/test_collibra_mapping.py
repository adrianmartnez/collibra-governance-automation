"""Unit tests for Collibra desired-state mapping (no network, no PostgreSQL)."""

from __future__ import annotations

import pytest

from governance.domain import (
    Column,
    Database,
    DataSource,
    ForeignKey,
    GovernanceModel,
    Ownership,
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
from governance.exporters import MetadataInventory
from governance.integrations.collibra import (
    CollibraMappingConfig,
    CollibraMappingError,
    map_to_desired_state,
    mock_mapping_config,
)


def _demo_shaped_model() -> GovernanceModel:
    source = "governance-demo"
    database = "governance_demo"
    schema = "commerce"

    customers_id = make_table_id(source, database, schema, "customers")
    orders_id = make_table_id(source, database, schema, "orders")
    customers_pk_col = make_column_id(source, database, schema, "customers", "customer_id")
    customers_email = make_column_id(source, database, schema, "customers", "email")
    orders_pk_col = make_column_id(source, database, schema, "orders", "order_id")
    orders_fk_col = make_column_id(source, database, schema, "orders", "customer_id")
    fk_id = make_foreign_key_id(orders_id, "orders_customer_fkey")

    customers = Table(
        id=customers_id,
        name="customers",
        schema_id=make_schema_id(source, database, schema),
        description="Customer master",
        ownership=Ownership(owner_name="governance_owner", owner_type="role"),
        columns=(
            Column(
                id=customers_pk_col,
                name="customer_id",
                data_type="uuid",
                ordinal_position=1,
                nullable=False,
                description="Stable customer identifier",
            ),
            Column(
                id=customers_email,
                name="email",
                data_type="character varying",
                ordinal_position=2,
                nullable=False,
                description="Customer email",
            ),
        ),
        primary_key=PrimaryKey(
            id=make_primary_key_id(customers_id, "customers_pkey"),
            name="customers_pkey",
            table_id=customers_id,
            column_ids=(customers_pk_col,),
        ),
    )
    orders = Table(
        id=orders_id,
        name="orders",
        schema_id=make_schema_id(source, database, schema),
        ownership=Ownership(owner_name="governance_owner", owner_type="role"),
        columns=(
            Column(
                id=orders_pk_col,
                name="order_id",
                data_type="bigint",
                ordinal_position=1,
                nullable=False,
            ),
            Column(
                id=orders_fk_col,
                name="customer_id",
                data_type="uuid",
                ordinal_position=2,
                nullable=False,
            ),
        ),
        primary_key=PrimaryKey(
            id=make_primary_key_id(orders_id, "orders_pkey"),
            name="orders_pkey",
            table_id=orders_id,
            column_ids=(orders_pk_col,),
        ),
        foreign_keys=(
            ForeignKey(
                id=fk_id,
                name="orders_customer_fkey",
                table_id=orders_id,
                column_ids=(orders_fk_col,),
                referenced_table_id=customers_id,
                referenced_column_ids=(customers_pk_col,),
            ),
        ),
    )
    return GovernanceModel(
        data_sources=(
            DataSource(
                id=make_datasource_id(source),
                name=source,
                system_type="postgresql",
                databases=(
                    Database(
                        id=make_database_id(source, database),
                        name=database,
                        datasource_id=make_datasource_id(source),
                        ownership=Ownership(owner_name="postgres", owner_type="role"),
                        schemas=(
                            Schema(
                                id=make_schema_id(source, database, schema),
                                name=schema,
                                database_id=make_database_id(source, database),
                                ownership=Ownership(
                                    owner_name="governance_owner",
                                    owner_type="role",
                                ),
                                tables=(customers, orders),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        relationships=(
            Relationship(
                id=make_relationship_id(fk_id),
                name="orders_customer_fkey",
                from_table_id=orders_id,
                to_table_id=customers_id,
                foreign_key_id=fk_id,
            ),
        ),
    )


def test_map_produces_all_asset_kinds_and_hierarchy_plus_fk() -> None:
    model = _demo_shaped_model()
    desired = map_to_desired_state(model, mock_mapping_config())

    by_type: dict[str, list[str]] = {}
    for asset in desired.assets:
        by_type.setdefault(asset.asset_type_ref, []).append(asset.local_id)

    config = mock_mapping_config()
    assert len(by_type[config.asset_type_refs["database"]]) == 1
    assert len(by_type[config.asset_type_refs["schema"]]) == 1
    assert len(by_type[config.asset_type_refs["table"]]) == 2
    assert len(by_type[config.asset_type_refs["column"]]) == 4

    relation_types = {rel.relation_type_ref for rel in desired.relationships}
    assert config.relation_type_refs["database_schema"] in relation_types
    assert config.relation_type_refs["schema_table"] in relation_types
    assert config.relation_type_refs["table_column"] in relation_types
    assert config.relation_type_refs["table_fk"] in relation_types

    fk_rels = [
        rel
        for rel in desired.relationships
        if rel.relation_type_ref == config.relation_type_refs["table_fk"]
    ]
    assert len(fk_rels) == 1
    assert fk_rels[0].source_local_id.endswith("/orders")
    assert fk_rels[0].target_local_id.endswith("/customers")


def test_full_names_unique_display_names_short_local_id_primary() -> None:
    desired = map_to_desired_state(_demo_shaped_model(), mock_mapping_config())
    names = [asset.name for asset in desired.assets]
    assert len(names) == len(set(names))

    db = next(asset for asset in desired.assets if asset.local_id.startswith("db:"))
    schema = next(asset for asset in desired.assets if asset.local_id.startswith("sch:"))
    table = next(asset for asset in desired.assets if asset.local_id.endswith("/customers"))
    column = next(asset for asset in desired.assets if asset.local_id.endswith("/customers/email"))

    assert db.name == "governance_demo"
    assert db.display_name == "governance_demo"
    assert schema.name == "governance_demo.commerce"
    assert schema.display_name == "commerce"
    assert table.name == "governance_demo.commerce.customers"
    assert table.display_name == "customers"
    assert column.name == "governance_demo.commerce.customers.email"
    assert column.display_name == "email"

    local_id_attr = mock_mapping_config().attribute_type_refs["local_id"]
    for asset in desired.assets:
        values = {attr.attribute_type_ref: attr.value for attr in asset.attributes}
        assert values[local_id_attr] == asset.local_id


def test_technical_ownership_and_description_attributes() -> None:
    config = mock_mapping_config()
    desired = map_to_desired_state(_demo_shaped_model(), config)
    email = next(asset for asset in desired.assets if asset.local_id.endswith("/customers/email"))
    values = {attr.attribute_type_ref: attr.value for attr in email.attributes}
    assert values[config.attribute_type_refs["data_type"]] == "character varying"
    assert values[config.attribute_type_refs["nullable"]] == "false"
    assert values[config.attribute_type_refs["ordinal_position"]] == "2"
    assert values[config.attribute_type_refs["description"]] == "Customer email"

    table = next(asset for asset in desired.assets if asset.local_id.endswith("/customers"))
    table_values = {attr.attribute_type_ref: attr.value for attr in table.attributes}
    assert table_values[config.attribute_type_refs["owner"]] == "governance_owner"
    assert table_values[config.attribute_type_refs["description"]] == "Customer master"


def test_mapping_is_deterministic_and_inventory_accepted() -> None:
    model = _demo_shaped_model()
    config = mock_mapping_config()
    first = map_to_desired_state(model, config)
    second = map_to_desired_state(MetadataInventory.from_model(model), config)
    assert first.to_json() == second.to_json()
    assert first.to_dict() == map_to_desired_state(model, config).to_dict()


def test_missing_config_keys_fail() -> None:
    with pytest.raises(CollibraMappingError, match="asset_type_refs missing"):
        CollibraMappingConfig(
            domain_ref="mock:domain",
            asset_type_refs={"database": "x"},
            relation_type_refs=mock_mapping_config().relation_type_refs,
            attribute_type_refs=mock_mapping_config().attribute_type_refs,
        )


def test_empty_ref_value_fails() -> None:
    refs = dict(mock_mapping_config().asset_type_refs)
    refs["table"] = "  "
    with pytest.raises(CollibraMappingError, match="non-empty"):
        CollibraMappingConfig(
            domain_ref="mock:domain",
            asset_type_refs=refs,
            relation_type_refs=mock_mapping_config().relation_type_refs,
            attribute_type_refs=mock_mapping_config().attribute_type_refs,
        )


def test_dangling_fk_relationship_fails() -> None:
    model = _demo_shaped_model()
    broken = GovernanceModel(
        data_sources=model.data_sources,
        relationships=(
            Relationship(
                id="rel:missing",
                name="missing",
                from_table_id=model.relationships[0].from_table_id,
                to_table_id="tbl:does-not-exist",
            ),
        ),
    )
    with pytest.raises(CollibraMappingError, match="target asset missing"):
        map_to_desired_state(broken, mock_mapping_config())


def test_mock_refs_are_clearly_mock_symbols() -> None:
    config = mock_mapping_config()
    assert config.domain_ref.startswith("mock:")
    assert all(value.startswith("mock:") for value in config.asset_type_refs.values())
    assert all(value.startswith("mock:") for value in config.relation_type_refs.values())
    assert all(value.startswith("mock:") for value in config.attribute_type_refs.values())


def test_relationship_source_and_target_exist() -> None:
    desired = map_to_desired_state(_demo_shaped_model(), mock_mapping_config())
    asset_ids = {asset.local_id for asset in desired.assets}
    for relationship in desired.relationships:
        assert relationship.source_local_id in asset_ids
        assert relationship.target_local_id in asset_ids
