"""Integration tests for PostgreSQL metadata discovery and inventory export."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from governance.config import load_settings
from governance.exporters import MetadataInventory
from governance.scanner import PostgresMetadataScanner
from governance.snapshots import GovernanceSnapshot, snapshot_to_json

pytestmark = pytest.mark.integration

COMMERCE_TABLES = {
    "customers",
    "products",
    "employees",
    "orders",
    "order_items",
    "payments",
    "marketing_contacts",
}


def _demo_settings():
    return load_settings(dotenv_path=None, environ={})


def _connect_autocommit(settings):
    connection = psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=5,
        row_factory=dict_row,
        autocommit=True,
    )
    return connection


def _read_column_comment(connection, schema: str, table: str, column: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT col_description(c.oid, a.attnum) AS description
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
            WHERE n.nspname = %s
              AND c.relname = %s
              AND a.attname = %s
              AND a.attnum > 0
              AND NOT a.attisdropped
            """,
            (schema, table, column),
        )
        row = cursor.fetchone()
        if row is None:
            raise AssertionError(f"column not found: {schema}.{table}.{column}")
        return row["description"]


def _set_column_comment(
    connection, schema: str, table: str, column: str, comment: str | None
) -> None:
    if comment is None:
        statement = sql.SQL("COMMENT ON COLUMN {}.{} IS NULL").format(
            sql.Identifier(schema, table),
            sql.Identifier(column),
        )
    else:
        statement = sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(
            sql.Identifier(schema, table),
            sql.Identifier(column),
            sql.Literal(comment),
        )
    with connection.cursor() as cursor:
        cursor.execute(statement)


def _catalog_counts(connection) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname AS table_name
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'commerce'
              AND c.relkind IN ('r', 'p')
            ORDER BY c.relname
            """
        )
        table_names = {row["table_name"] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT COUNT(*) AS column_count
            FROM pg_catalog.pg_attribute AS a
            JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'commerce'
              AND c.relkind IN ('r', 'p')
              AND a.attnum > 0
              AND NOT a.attisdropped
            """
        )
        column_count = int(cursor.fetchone()["column_count"])

        cursor.execute(
            """
            SELECT COUNT(*) AS pk_count
            FROM pg_catalog.pg_constraint AS con
            JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE con.contype = 'p'
              AND n.nspname = 'commerce'
              AND c.relkind IN ('r', 'p')
            """
        )
        pk_count = int(cursor.fetchone()["pk_count"])

        cursor.execute(
            """
            SELECT COUNT(*) AS fk_count
            FROM pg_catalog.pg_constraint AS con
            JOIN pg_catalog.pg_class AS sc ON sc.oid = con.conrelid
            JOIN pg_catalog.pg_namespace AS sn ON sn.oid = sc.relnamespace
            JOIN pg_catalog.pg_class AS rc ON rc.oid = con.confrelid
            JOIN pg_catalog.pg_namespace AS rn ON rn.oid = rc.relnamespace
            WHERE con.contype = 'f'
              AND sn.nspname = 'commerce'
              AND rn.nspname = 'commerce'
              AND sc.relkind IN ('r', 'p')
              AND rc.relkind IN ('r', 'p')
            """
        )
        fk_count = int(cursor.fetchone()["fk_count"])

    return {
        "table_names": table_names,
        "column_count": column_count,
        "pk_count": pk_count,
        "fk_count": fk_count,
    }


def test_postgres_metadata_pipeline_discovery_determinism_and_mutation() -> None:
    settings = _demo_settings()
    scanner = PostgresMetadataScanner(settings)
    mutation_connection = _connect_autocommit(settings)
    assert mutation_connection.autocommit is True

    original_comment = _read_column_comment(mutation_connection, "commerce", "customers", "email")
    temporary_comment = "Updated metadata integration comment"

    try:
        catalog = _catalog_counts(mutation_connection)

        model1 = scanner.scan()
        inventory1 = MetadataInventory.from_model(model1)
        model2 = scanner.scan()
        inventory2 = MetadataInventory.from_model(model2)

        assert model1.to_json() == model2.to_json()
        assert inventory1.to_json().encode("utf-8") == inventory2.to_json().encode("utf-8")

        snapshot1 = GovernanceSnapshot.from_model(model1)
        snapshot2 = GovernanceSnapshot.from_model(model2)
        assert snapshot_to_json(snapshot1).encode("utf-8") == snapshot_to_json(snapshot2).encode(
            "utf-8"
        )
        assert snapshot1.content_identity() == snapshot2.content_identity()

        data_source = model1.data_sources[0]
        assert data_source.name == "governance-demo"
        assert data_source.system_type == "postgresql"
        assert len(data_source.databases) == 1
        database = data_source.databases[0]
        assert database.name == "governance_demo"

        schema_names = {schema.name for schema in database.schemas}
        assert "commerce" in schema_names
        assert "pg_catalog" not in schema_names
        assert "information_schema" not in schema_names

        commerce = next(schema for schema in database.schemas if schema.name == "commerce")
        discovered_tables = {table.name for table in commerce.tables}
        assert discovered_tables == COMMERCE_TABLES
        assert discovered_tables == catalog["table_names"]

        commerce_columns = [column for table in commerce.tables for column in table.columns]
        assert len(commerce_columns) == catalog["column_count"]
        assert all(column.ordinal_position >= 1 for column in commerce_columns)

        customers = next(table for table in commerce.tables if table.name == "customers")
        email = next(column for column in customers.columns if column.name == "email")
        assert email.data_type in {"character varying(255)", "varchar(255)"}
        assert customers.description is not None
        assert email.description is not None
        assert customers.ownership is not None
        assert customers.ownership.owner_name == "governance_owner"

        pk_count = sum(1 for table in commerce.tables if table.primary_key is not None)
        fk_count = sum(len(table.foreign_keys) for table in commerce.tables)
        assert pk_count == catalog["pk_count"]
        assert fk_count == catalog["fk_count"]
        assert len(model1.relationships) == fk_count

        employees = next(table for table in commerce.tables if table.name == "employees")
        self_fks = [
            foreign_key
            for foreign_key in employees.foreign_keys
            if foreign_key.table_id == foreign_key.referenced_table_id
            and any(column_id.endswith("/manager_id") for column_id in foreign_key.column_ids)
        ]
        assert len(self_fks) == 1
        assert any(
            relationship.foreign_key_id == self_fks[0].id for relationship in model1.relationships
        )

        # Runtime transaction contract is enforced inside scanner query #1.
        assert "transaction_isolation" not in model1.to_json()
        assert "transaction_read_only" not in model1.to_json()

        _set_column_comment(
            mutation_connection,
            "commerce",
            "customers",
            "email",
            temporary_comment,
        )

        model3 = scanner.scan()
        inventory3 = MetadataInventory.from_model(model3)
        commerce3 = next(
            schema
            for schema in model3.data_sources[0].databases[0].schemas
            if schema.name == "commerce"
        )
        customers3 = next(table for table in commerce3.tables if table.name == "customers")
        email3 = next(column for column in customers3.columns if column.name == "email")
        assert email3.description == temporary_comment
        assert inventory3.to_json() != inventory2.to_json()
    finally:
        _set_column_comment(
            mutation_connection,
            "commerce",
            "customers",
            "email",
            original_comment,
        )
        restored = _read_column_comment(mutation_connection, "commerce", "customers", "email")
        mutation_connection.close()
        assert restored == original_comment
