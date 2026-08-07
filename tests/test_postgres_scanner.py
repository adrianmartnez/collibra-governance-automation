"""Unit tests for PostgreSQL metadata discovery mapping helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from governance.config import Settings
from governance.domain import make_relationship_id
from governance.scanner.postgres import (
    MetadataDiscoveryError,
    PostgresMetadataScanner,
    _is_user_schema,
    build_governance_model,
)


def _settings(
    *,
    host: str = "localhost",
    port: int = 5432,
    source_name: str = "governance-demo",
    password: str = "super-secret-password",
) -> Settings:
    return Settings(
        postgres_host=host,
        postgres_port=port,
        postgres_db="governance_demo",
        postgres_user="postgres",
        postgres_password=password,
        postgres_source_name=source_name,
        inventory_output_path="artifacts/metadata-inventory.json",
    )


def _database_row(name: str = "governance_demo") -> dict[str, Any]:
    return {
        "database_name": name,
        "owner_name": "postgres",
        "description": None,
        "transaction_isolation": "repeatable read",
        "transaction_read_only": "on",
    }


def _column_row(
    schema: str,
    table: str,
    column: str,
    ordinal: int,
    *,
    data_type: str = "integer",
    nullable: bool = False,
) -> dict[str, Any]:
    return {
        "schema_name": schema,
        "table_name": table,
        "table_owner": "governance_owner",
        "table_description": f"{table} table",
        "column_name": column,
        "ordinal_position": ordinal,
        "data_type": data_type,
        "nullable": nullable,
        "column_description": None,
    }


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("commerce", True),
        ("public", True),
        ("pg_catalog", False),
        ("pg_toast", False),
        ("pg_temp_3", False),
        ("information_schema", False),
    ],
)
def test_is_user_schema(name: str, expected: bool) -> None:
    assert _is_user_schema(name) is expected


def test_stable_ids_ignore_host_and_port() -> None:
    schema_rows = [{"schema_name": "sales", "owner_name": "owner", "description": None}]
    table_column_rows = [
        _column_row("sales", "orders", "id", 1),
        _column_row("sales", "orders", "amount", 2, nullable=True),
    ]
    model_a = build_governance_model(
        source_name="governance-demo",
        database_row=_database_row(),
        schema_rows=schema_rows,
        table_column_rows=table_column_rows,
        primary_key_rows=[
            {
                "schema_name": "sales",
                "table_name": "orders",
                "constraint_name": "orders_pkey",
                "column_name": "id",
                "key_ordinal": 1,
            }
        ],
        foreign_key_rows=[],
    )
    model_b = build_governance_model(
        source_name="governance-demo",
        database_row=_database_row(),
        schema_rows=schema_rows,
        table_column_rows=table_column_rows,
        primary_key_rows=[
            {
                "schema_name": "sales",
                "table_name": "orders",
                "constraint_name": "orders_pkey",
                "column_name": "id",
                "key_ordinal": 1,
            }
        ],
        foreign_key_rows=[],
    )
    assert model_a.to_json() == model_b.to_json()
    assert _settings(host="db-a", port=1111).postgres_source_name == "governance-demo"
    assert _settings(host="db-b", port=2222).postgres_source_name == "governance-demo"
    assert model_a.data_sources[0].id == "ds:governance-demo"


def test_composite_primary_key_preserves_constraint_ordinality() -> None:
    model = build_governance_model(
        source_name="governance-demo",
        database_row=_database_row(),
        schema_rows=[{"schema_name": "sales", "owner_name": "owner", "description": None}],
        table_column_rows=[
            _column_row("sales", "line_items", "sku", 1),
            _column_row("sales", "line_items", "region", 2),
            _column_row("sales", "line_items", "batch", 3),
        ],
        primary_key_rows=[
            {
                "schema_name": "sales",
                "table_name": "line_items",
                "constraint_name": "line_items_pkey",
                "column_name": "batch",
                "key_ordinal": 1,
            },
            {
                "schema_name": "sales",
                "table_name": "line_items",
                "constraint_name": "line_items_pkey",
                "column_name": "sku",
                "key_ordinal": 2,
            },
        ],
        foreign_key_rows=[],
    )
    table = model.data_sources[0].databases[0].schemas[0].tables[0]
    assert table.primary_key is not None
    assert [column_id.split("/")[-1] for column_id in table.primary_key.column_ids] == [
        "batch",
        "sku",
    ]


def test_composite_foreign_key_preserves_pairing() -> None:
    model = build_governance_model(
        source_name="governance-demo",
        database_row=_database_row(),
        schema_rows=[
            {"schema_name": "sales", "owner_name": "owner", "description": None},
        ],
        table_column_rows=[
            _column_row("sales", "parents", "a", 1),
            _column_row("sales", "parents", "b", 2),
            _column_row("sales", "children", "x", 1),
            _column_row("sales", "children", "y", 2),
        ],
        primary_key_rows=[
            {
                "schema_name": "sales",
                "table_name": "parents",
                "constraint_name": "parents_pkey",
                "column_name": "a",
                "key_ordinal": 1,
            },
            {
                "schema_name": "sales",
                "table_name": "parents",
                "constraint_name": "parents_pkey",
                "column_name": "b",
                "key_ordinal": 2,
            },
        ],
        foreign_key_rows=[
            {
                "schema_name": "sales",
                "table_name": "children",
                "constraint_name": "children_parent_fkey",
                "column_name": "x",
                "referenced_schema_name": "sales",
                "referenced_table_name": "parents",
                "referenced_column_name": "a",
                "key_ordinal": 1,
            },
            {
                "schema_name": "sales",
                "table_name": "children",
                "constraint_name": "children_parent_fkey",
                "column_name": "y",
                "referenced_schema_name": "sales",
                "referenced_table_name": "parents",
                "referenced_column_name": "b",
                "key_ordinal": 2,
            },
        ],
    )
    children = next(
        table
        for table in model.data_sources[0].databases[0].schemas[0].tables
        if table.name == "children"
    )
    assert len(children.foreign_keys) == 1
    foreign_key = children.foreign_keys[0]
    assert [column_id.split("/")[-1] for column_id in foreign_key.column_ids] == ["x", "y"]
    assert [column_id.split("/")[-1] for column_id in foreign_key.referenced_column_ids] == [
        "a",
        "b",
    ]


def test_self_foreign_key_creates_exact_relationship() -> None:
    model = build_governance_model(
        source_name="governance-demo",
        database_row=_database_row(),
        schema_rows=[{"schema_name": "hr", "owner_name": "owner", "description": None}],
        table_column_rows=[
            _column_row("hr", "staff", "id", 1),
            _column_row("hr", "staff", "manager_id", 2, nullable=True),
        ],
        primary_key_rows=[
            {
                "schema_name": "hr",
                "table_name": "staff",
                "constraint_name": "staff_pkey",
                "column_name": "id",
                "key_ordinal": 1,
            }
        ],
        foreign_key_rows=[
            {
                "schema_name": "hr",
                "table_name": "staff",
                "constraint_name": "staff_manager_fkey",
                "column_name": "manager_id",
                "referenced_schema_name": "hr",
                "referenced_table_name": "staff",
                "referenced_column_name": "id",
                "key_ordinal": 1,
            }
        ],
    )
    table = model.data_sources[0].databases[0].schemas[0].tables[0]
    assert len(table.foreign_keys) == 1
    foreign_key = table.foreign_keys[0]
    assert foreign_key.table_id == foreign_key.referenced_table_id
    assert len(model.relationships) == 1
    relationship = model.relationships[0]
    assert relationship.from_table_id == relationship.to_table_id == table.id
    assert relationship.foreign_key_id == foreign_key.id
    assert relationship.id == make_relationship_id(foreign_key.id)
    assert relationship.name == foreign_key.name


def test_cross_schema_user_foreign_key_included() -> None:
    model = build_governance_model(
        source_name="governance-demo",
        database_row=_database_row(),
        schema_rows=[
            {"schema_name": "sales", "owner_name": "owner", "description": None},
            {"schema_name": "shared", "owner_name": "owner", "description": None},
        ],
        table_column_rows=[
            _column_row("shared", "parties", "party_id", 1),
            _column_row("sales", "deals", "deal_id", 1),
            _column_row("sales", "deals", "party_id", 2),
        ],
        primary_key_rows=[
            {
                "schema_name": "shared",
                "table_name": "parties",
                "constraint_name": "parties_pkey",
                "column_name": "party_id",
                "key_ordinal": 1,
            },
            {
                "schema_name": "sales",
                "table_name": "deals",
                "constraint_name": "deals_pkey",
                "column_name": "deal_id",
                "key_ordinal": 1,
            },
        ],
        foreign_key_rows=[
            {
                "schema_name": "sales",
                "table_name": "deals",
                "constraint_name": "deals_party_fkey",
                "column_name": "party_id",
                "referenced_schema_name": "shared",
                "referenced_table_name": "parties",
                "referenced_column_name": "party_id",
                "key_ordinal": 1,
            }
        ],
    )
    assert len(model.relationships) == 1
    deals = next(
        table
        for schema in model.data_sources[0].databases[0].schemas
        for table in schema.tables
        if table.name == "deals"
    )
    assert len(deals.foreign_keys) == 1
    assert "shared" in deals.foreign_keys[0].referenced_table_id


def test_excluded_schema_reference_does_not_create_dangling_fk() -> None:
    model = build_governance_model(
        source_name="governance-demo",
        database_row=_database_row(),
        schema_rows=[{"schema_name": "sales", "owner_name": "owner", "description": None}],
        table_column_rows=[
            _column_row("sales", "deals", "deal_id", 1),
            _column_row("sales", "deals", "party_id", 2),
        ],
        primary_key_rows=[
            {
                "schema_name": "sales",
                "table_name": "deals",
                "constraint_name": "deals_pkey",
                "column_name": "deal_id",
                "key_ordinal": 1,
            }
        ],
        foreign_key_rows=[
            {
                "schema_name": "sales",
                "table_name": "deals",
                "constraint_name": "deals_party_fkey",
                "column_name": "party_id",
                "referenced_schema_name": "pg_catalog",
                "referenced_table_name": "pg_class",
                "referenced_column_name": "oid",
                "key_ordinal": 1,
            }
        ],
    )
    deals = model.data_sources[0].databases[0].schemas[0].tables[0]
    assert deals.foreign_keys == ()
    assert model.relationships == ()


def test_zero_column_table_raises_sanitized_error() -> None:
    with pytest.raises(
        MetadataDiscoveryError,
        match="PostgreSQL metadata discovery found a table with no representable columns",
    ):
        build_governance_model(
            source_name="governance-demo",
            database_row=_database_row(),
            schema_rows=[{"schema_name": "sales", "owner_name": "owner", "description": None}],
            table_column_rows=[
                {
                    "schema_name": "sales",
                    "table_name": "empty_table",
                    "table_owner": "owner",
                    "table_description": None,
                    "column_name": None,
                    "ordinal_position": None,
                    "data_type": None,
                    "nullable": None,
                    "column_description": None,
                }
            ],
            primary_key_rows=[],
            foreign_key_rows=[],
        )


def test_driver_error_is_sanitized() -> None:
    settings = _settings(password="super-secret-password")
    scanner = PostgresMetadataScanner(settings)
    with patch("governance.scanner.postgres.psycopg.connect") as connect:
        connect.side_effect = RuntimeError("connection failed using password=super-secret-password")
        with pytest.raises(
            MetadataDiscoveryError, match="PostgreSQL metadata discovery failed"
        ) as exc:
            scanner.scan()
    assert "super-secret-password" not in str(exc.value)
    assert exc.value.__cause__ is None


def test_empty_user_schema_is_preserved() -> None:
    model = build_governance_model(
        source_name="governance-demo",
        database_row=_database_row(),
        schema_rows=[
            {"schema_name": "empty_schema", "owner_name": "owner", "description": "empty"},
            {"schema_name": "sales", "owner_name": "owner", "description": None},
        ],
        table_column_rows=[_column_row("sales", "orders", "id", 1)],
        primary_key_rows=[],
        foreign_key_rows=[],
    )
    schema_names = {schema.name for schema in model.data_sources[0].databases[0].schemas}
    assert "empty_schema" in schema_names
    empty = next(
        schema
        for schema in model.data_sources[0].databases[0].schemas
        if schema.name == "empty_schema"
    )
    assert empty.tables == ()


def test_scan_executes_exactly_five_catalog_queries() -> None:
    settings = _settings()
    scanner = PostgresMetadataScanner(settings)

    cursor = MagicMock()
    cursor.fetchone.return_value = _database_row()
    cursor.fetchall.side_effect = [
        [{"schema_name": "sales", "owner_name": "owner", "description": None}],
        [_column_row("sales", "orders", "id", 1)],
        [],
        [],
    ]

    transaction_cm = MagicMock()
    transaction_cm.__enter__.return_value = transaction_cm
    transaction_cm.__exit__.return_value = None

    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    cursor_cm.__exit__.return_value = None

    connection = MagicMock()
    connection.transaction.return_value = transaction_cm
    connection.cursor.return_value = cursor_cm

    connect_cm = MagicMock()
    connect_cm.__enter__.return_value = connection
    connect_cm.__exit__.return_value = None

    with patch("governance.scanner.postgres.psycopg.connect", return_value=connect_cm):
        model = scanner.scan()

    executed = [call.args[0] for call in cursor.execute.call_args_list]
    assert executed[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert executed[1] == "SET LOCAL statement_timeout = '10000ms'"
    catalog_sql = executed[2:]
    assert len(catalog_sql) == 5
    assert model.data_sources[0].name == "governance-demo"
