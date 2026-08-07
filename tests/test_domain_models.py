"""Focused tests for the vendor-neutral governance domain model."""

from __future__ import annotations

import ast
from pathlib import Path

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

FORBIDDEN_IMPORT_ROOTS = {
    "psycopg",
    "psycopg2",
    "sqlalchemy",
    "httpx",
    "requests",
    "urllib3",
}


def _build_sample_model() -> GovernanceModel:
    ds_name = "demo-pg"
    db_name = "governance_demo"
    schema_name = "commerce"

    customers_id = make_table_id(ds_name, db_name, schema_name, "customers")
    orders_id = make_table_id(ds_name, db_name, schema_name, "orders")

    customer_id_col = Column(
        id=make_column_id(ds_name, db_name, schema_name, "customers", "customer_id"),
        name="customer_id",
        data_type="uuid",
        ordinal_position=1,
        nullable=False,
        description="Stable customer identifier",
    )
    customer_email_col = Column(
        id=make_column_id(ds_name, db_name, schema_name, "customers", "email"),
        name="email",
        data_type="varchar",
        ordinal_position=2,
        nullable=False,
        description="Fictional customer email",
    )
    order_id_col = Column(
        id=make_column_id(ds_name, db_name, schema_name, "orders", "order_id"),
        name="order_id",
        data_type="bigint",
        ordinal_position=1,
        nullable=False,
    )
    order_customer_col = Column(
        id=make_column_id(ds_name, db_name, schema_name, "orders", "customer_id"),
        name="customer_id",
        data_type="uuid",
        ordinal_position=2,
        nullable=False,
    )

    customers_pk = PrimaryKey(
        id=make_primary_key_id(customers_id, "customers_pkey"),
        name="customers_pkey",
        table_id=customers_id,
        column_ids=(customer_id_col.id,),
    )
    orders_pk = PrimaryKey(
        id=make_primary_key_id(orders_id, "orders_pkey"),
        name="orders_pkey",
        table_id=orders_id,
        column_ids=(order_id_col.id,),
    )
    orders_fk = ForeignKey(
        id=make_foreign_key_id(orders_id, "orders_customer_id_fkey"),
        name="orders_customer_id_fkey",
        table_id=orders_id,
        column_ids=(order_customer_col.id,),
        referenced_table_id=customers_id,
        referenced_column_ids=(customer_id_col.id,),
    )

    # Intentionally reverse insertion order to assert deterministic sorting.
    orders = Table(
        id=orders_id,
        name="orders",
        schema_id=make_schema_id(ds_name, db_name, schema_name),
        columns=(order_customer_col, order_id_col),
        primary_key=orders_pk,
        foreign_keys=(orders_fk,),
        description="Customer orders",
        ownership=Ownership(owner_name="governance_owner", owner_type="role"),
    )
    customers = Table(
        id=customers_id,
        name="customers",
        schema_id=make_schema_id(ds_name, db_name, schema_name),
        columns=(customer_email_col, customer_id_col),
        primary_key=customers_pk,
        description="Registered customers",
        ownership=Ownership(owner_name="governance_owner", owner_type="role"),
    )

    schema = Schema(
        id=make_schema_id(ds_name, db_name, schema_name),
        name=schema_name,
        database_id=make_database_id(ds_name, db_name),
        tables=(orders, customers),
        description="Commercial transactional schema",
        ownership=Ownership(owner_name="governance_owner", owner_type="role"),
    )
    database = Database(
        id=make_database_id(ds_name, db_name),
        name=db_name,
        datasource_id=make_datasource_id(ds_name),
        schemas=(schema,),
        description="Demo governance database",
    )
    datasource = DataSource(
        id=make_datasource_id(ds_name),
        name=ds_name,
        system_type="postgresql",
        databases=(database,),
        description="Local PostgreSQL demo source",
        technical_attributes={"host": "localhost", "port": 5432},
    )
    relationship = Relationship(
        id=make_relationship_id("orders_to_customers"),
        name="orders_to_customers",
        from_table_id=orders_id,
        to_table_id=customers_id,
        foreign_key_id=orders_fk.id,
        description="Orders belong to customers",
    )
    return GovernanceModel(data_sources=(datasource,), relationships=(relationship,))


def test_stable_identifiers() -> None:
    assert make_datasource_id("demo-pg") == "ds:demo-pg"
    assert make_database_id("demo-pg", "governance_demo") == "db:demo-pg/governance_demo"
    assert (
        make_schema_id("demo-pg", "governance_demo", "commerce")
        == "sch:demo-pg/governance_demo/commerce"
    )


def test_deterministic_serialization_and_ordering() -> None:
    model_a = _build_sample_model()
    model_b = _build_sample_model()
    assert model_a == model_b
    assert model_a.to_json() == model_b.to_json()

    payload = model_a.to_dict()
    table_ids = [
        table["id"] for table in payload["data_sources"][0]["databases"][0]["schemas"][0]["tables"]
    ]
    assert table_ids == sorted(table_ids)

    customer_columns = payload["data_sources"][0]["databases"][0]["schemas"][0]["tables"][0][
        "columns"
    ]
    ordinals = [column["ordinal_position"] for column in customer_columns]
    assert ordinals == sorted(ordinals)


def test_required_fields_rejected() -> None:
    with pytest.raises(ValueError, match="name is required"):
        DataSource(id="ds:x", name="", system_type="postgresql")
    with pytest.raises(ValueError, match="columns must not be empty"):
        Table(
            id="tbl:x",
            name="t",
            schema_id="sch:x",
            columns=(),
        )
    with pytest.raises(ValueError, match="column_ids must not be empty"):
        PrimaryKey(id="pk:x", name="pk", table_id="tbl:x", column_ids=())


def test_domain_module_has_no_forbidden_imports() -> None:
    models_path = (
        Path(__file__).resolve().parents[1] / "src" / "governance" / "domain" / "models.py"
    )
    tree = ast.parse(models_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    source = models_path.read_text(encoding="utf-8")
    assert imported.isdisjoint(FORBIDDEN_IMPORT_ROOTS)
    assert "governance.integrations" not in source
    assert "import psycopg" not in source
    assert "import sqlalchemy" not in source
    assert "import httpx" not in source
    assert "import requests" not in source
