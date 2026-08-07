"""PostgreSQL system-catalog metadata discovery."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import psycopg
from psycopg.rows import dict_row

from governance.config import Settings
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

CATALOG_QUERY_COUNT = 5

_SQL_DATABASE_METADATA = """
SELECT
    current_database() AS database_name,
    pg_get_userbyid(d.datdba) AS owner_name,
    shobj_description(d.oid, 'pg_database') AS description,
    current_setting('transaction_isolation') AS transaction_isolation,
    current_setting('transaction_read_only') AS transaction_read_only
FROM pg_catalog.pg_database AS d
WHERE d.datname = current_database()
"""

_SQL_SCHEMAS_METADATA = """
SELECT
    n.nspname AS schema_name,
    pg_get_userbyid(n.nspowner) AS owner_name,
    obj_description(n.oid, 'pg_namespace') AS description
FROM pg_catalog.pg_namespace AS n
WHERE n.nspname <> 'information_schema'
  AND n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'
ORDER BY n.nspname
"""

_SQL_TABLES_COLUMNS_METADATA = """
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    pg_get_userbyid(c.relowner) AS table_owner,
    obj_description(c.oid, 'pg_class') AS table_description,
    a.attname AS column_name,
    a.attnum AS ordinal_position,
    format_type(a.atttypid, a.atttypmod) AS data_type,
    NOT a.attnotnull AS nullable,
    col_description(c.oid, a.attnum) AS column_description
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_catalog.pg_attribute AS a
    ON a.attrelid = c.oid
   AND a.attnum > 0
   AND NOT a.attisdropped
WHERE c.relkind IN ('r', 'p')
  AND n.nspname <> 'information_schema'
  AND n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'
ORDER BY n.nspname, c.relname, a.attnum NULLS LAST
"""

_SQL_PRIMARY_KEYS_METADATA = """
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    con.conname AS constraint_name,
    a.attname AS column_name,
    ord.ordinality AS key_ordinal
FROM pg_catalog.pg_constraint AS con
JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS ord(attnum, ordinality) ON TRUE
JOIN pg_catalog.pg_attribute AS a
    ON a.attrelid = con.conrelid
   AND a.attnum = ord.attnum
WHERE con.contype = 'p'
  AND c.relkind IN ('r', 'p')
  AND n.nspname <> 'information_schema'
  AND n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'
ORDER BY n.nspname, c.relname, con.conname, ord.ordinality
"""

_SQL_FOREIGN_KEYS_METADATA = """
SELECT
    sn.nspname AS schema_name,
    sc.relname AS table_name,
    con.conname AS constraint_name,
    sa.attname AS column_name,
    rn.nspname AS referenced_schema_name,
    rc.relname AS referenced_table_name,
    ra.attname AS referenced_column_name,
    ord.ordinality AS key_ordinal
FROM pg_catalog.pg_constraint AS con
JOIN pg_catalog.pg_class AS sc ON sc.oid = con.conrelid
JOIN pg_catalog.pg_namespace AS sn ON sn.oid = sc.relnamespace
JOIN pg_catalog.pg_class AS rc ON rc.oid = con.confrelid
JOIN pg_catalog.pg_namespace AS rn ON rn.oid = rc.relnamespace
JOIN LATERAL unnest(con.conkey, con.confkey)
    WITH ORDINALITY AS ord(attnum, confattnum, ordinality) ON TRUE
JOIN pg_catalog.pg_attribute AS sa
    ON sa.attrelid = con.conrelid
   AND sa.attnum = ord.attnum
JOIN pg_catalog.pg_attribute AS ra
    ON ra.attrelid = con.confrelid
   AND ra.attnum = ord.confattnum
WHERE con.contype = 'f'
  AND sc.relkind IN ('r', 'p')
  AND rc.relkind IN ('r', 'p')
  AND sn.nspname <> 'information_schema'
  AND sn.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'
  AND rn.nspname <> 'information_schema'
  AND rn.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'
ORDER BY sn.nspname, sc.relname, con.conname, ord.ordinality
"""


class MetadataDiscoveryError(RuntimeError):
    """Raised when PostgreSQL metadata discovery fails."""


def _is_user_schema(name: str) -> bool:
    return name != "information_schema" and not name.startswith("pg_")


def _ownership(owner_name: str | None) -> Ownership | None:
    if owner_name is None or not str(owner_name).strip():
        return None
    return Ownership(owner_name=str(owner_name), owner_type="role")


def _group_constraint_columns(
    rows: list[dict[str, Any]],
    *,
    key_fields: tuple[str, ...],
) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(row[field] for field in key_fields)
        grouped[key].append(row)
    for key in grouped:
        grouped[key].sort(key=lambda item: int(item["key_ordinal"]))
    return grouped


def _validate_graph_integrity(model: GovernanceModel) -> None:
    tables: dict[str, Table] = {}
    columns_by_table: dict[str, set[str]] = {}
    foreign_keys: dict[str, ForeignKey] = {}

    for data_source in model.data_sources:
        for database in data_source.databases:
            for schema in database.schemas:
                for table in schema.tables:
                    tables[table.id] = table
                    columns_by_table[table.id] = {column.id for column in table.columns}
                    for foreign_key in table.foreign_keys:
                        foreign_keys[foreign_key.id] = foreign_key

    for table in tables.values():
        if table.primary_key is not None:
            pk = table.primary_key
            if pk.table_id not in tables:
                raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")
            table_columns = columns_by_table[pk.table_id]
            for column_id in pk.column_ids:
                if column_id not in table_columns:
                    raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")

        for foreign_key in table.foreign_keys:
            if foreign_key.table_id not in tables:
                raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")
            if foreign_key.referenced_table_id not in tables:
                raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")
            if len(foreign_key.column_ids) != len(foreign_key.referenced_column_ids):
                raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")
            source_columns = columns_by_table[foreign_key.table_id]
            referenced_columns = columns_by_table[foreign_key.referenced_table_id]
            for column_id in foreign_key.column_ids:
                if column_id not in source_columns:
                    raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")
            for column_id in foreign_key.referenced_column_ids:
                if column_id not in referenced_columns:
                    raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")

    for relationship in model.relationships:
        if relationship.from_table_id not in tables:
            raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")
        if relationship.to_table_id not in tables:
            raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")
        if relationship.foreign_key_id is None or relationship.foreign_key_id not in foreign_keys:
            raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")


def build_governance_model(
    *,
    source_name: str,
    database_row: dict[str, Any],
    schema_rows: list[dict[str, Any]],
    table_column_rows: list[dict[str, Any]],
    primary_key_rows: list[dict[str, Any]],
    foreign_key_rows: list[dict[str, Any]],
) -> GovernanceModel:
    """Map bulk catalog rows into a GovernanceModel (testable without a live database)."""
    isolation = str(database_row.get("transaction_isolation", "")).lower()
    read_only = str(database_row.get("transaction_read_only", "")).lower()
    if isolation != "repeatable read" or read_only != "on":
        raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")

    database_name = str(database_row["database_name"])
    data_source_id = make_datasource_id(source_name)
    database_id = make_database_id(source_name, database_name)

    schemas_meta: dict[str, dict[str, Any]] = {}
    for row in schema_rows:
        schema_name = str(row["schema_name"])
        if not _is_user_schema(schema_name):
            continue
        schemas_meta[schema_name] = row

    tables_meta: dict[tuple[str, str], dict[str, Any]] = {}
    columns_by_table: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in table_column_rows:
        schema_name = str(row["schema_name"])
        table_name = str(row["table_name"])
        if not _is_user_schema(schema_name):
            continue
        key = (schema_name, table_name)
        if key not in tables_meta:
            tables_meta[key] = {
                "schema_name": schema_name,
                "table_name": table_name,
                "table_owner": row.get("table_owner"),
                "table_description": row.get("table_description"),
            }
        if row.get("column_name") is not None:
            columns_by_table[key].append(row)

    for key in tables_meta:
        if not columns_by_table[key]:
            raise MetadataDiscoveryError(
                "PostgreSQL metadata discovery found a table with no representable columns"
            )

    table_ids: dict[tuple[str, str], str] = {}
    column_ids: dict[tuple[str, str, str], str] = {}
    columns_models: dict[tuple[str, str], tuple[Column, ...]] = {}

    for (schema_name, table_name), _table_meta in sorted(tables_meta.items()):
        table_id = make_table_id(source_name, database_name, schema_name, table_name)
        table_ids[(schema_name, table_name)] = table_id
        columns: list[Column] = []
        for column_row in sorted(
            columns_by_table[(schema_name, table_name)],
            key=lambda item: int(item["ordinal_position"]),
        ):
            column_name = str(column_row["column_name"])
            column_id = make_column_id(
                source_name, database_name, schema_name, table_name, column_name
            )
            column_ids[(schema_name, table_name, column_name)] = column_id
            columns.append(
                Column(
                    id=column_id,
                    name=column_name,
                    data_type=str(column_row["data_type"]),
                    ordinal_position=int(column_row["ordinal_position"]),
                    nullable=bool(column_row["nullable"]),
                    description=column_row.get("column_description"),
                    technical_attributes={},
                )
            )
        columns_models[(schema_name, table_name)] = tuple(columns)

    pk_groups = _group_constraint_columns(
        [row for row in primary_key_rows if _is_user_schema(str(row["schema_name"]))],
        key_fields=("schema_name", "table_name", "constraint_name"),
    )
    fk_groups = _group_constraint_columns(
        [
            row
            for row in foreign_key_rows
            if _is_user_schema(str(row["schema_name"]))
            and _is_user_schema(str(row["referenced_schema_name"]))
        ],
        key_fields=("schema_name", "table_name", "constraint_name"),
    )

    primary_keys: dict[tuple[str, str], PrimaryKey] = {}
    for (schema_name, table_name, constraint_name), pk_rows in pk_groups.items():
        table_key = (str(schema_name), str(table_name))
        if table_key not in table_ids:
            continue
        table_id = table_ids[table_key]
        try:
            pk_column_ids = tuple(
                column_ids[(str(schema_name), str(table_name), str(row["column_name"]))]
                for row in pk_rows
            )
        except KeyError:
            raise MetadataDiscoveryError("PostgreSQL metadata discovery failed") from None
        primary_keys[table_key] = PrimaryKey(
            id=make_primary_key_id(table_id, str(constraint_name)),
            name=str(constraint_name),
            table_id=table_id,
            column_ids=pk_column_ids,
        )

    foreign_keys_by_table: dict[tuple[str, str], list[ForeignKey]] = defaultdict(list)
    relationships: list[Relationship] = []
    for (schema_name, table_name, constraint_name), fk_rows in fk_groups.items():
        source_key = (str(schema_name), str(table_name))
        if source_key not in table_ids:
            continue
        referenced_schema = str(fk_rows[0]["referenced_schema_name"])
        referenced_table = str(fk_rows[0]["referenced_table_name"])
        referenced_key = (referenced_schema, referenced_table)
        if referenced_key not in table_ids:
            continue
        source_column_names = [str(row["column_name"]) for row in fk_rows]
        referenced_column_names = [str(row["referenced_column_name"]) for row in fk_rows]
        if len(source_column_names) != len(referenced_column_names):
            raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")
        try:
            source_ids = tuple(
                column_ids[(source_key[0], source_key[1], name)] for name in source_column_names
            )
            referenced_ids = tuple(
                column_ids[(referenced_schema, referenced_table, name)]
                for name in referenced_column_names
            )
        except KeyError:
            continue
        foreign_key = ForeignKey(
            id=make_foreign_key_id(table_ids[source_key], str(constraint_name)),
            name=str(constraint_name),
            table_id=table_ids[source_key],
            column_ids=source_ids,
            referenced_table_id=table_ids[referenced_key],
            referenced_column_ids=referenced_ids,
        )
        foreign_keys_by_table[source_key].append(foreign_key)
        relationships.append(
            Relationship(
                id=make_relationship_id(foreign_key.id),
                name=foreign_key.name,
                from_table_id=foreign_key.table_id,
                to_table_id=foreign_key.referenced_table_id,
                foreign_key_id=foreign_key.id,
                description=None,
            )
        )

    tables_by_schema: dict[str, list[Table]] = defaultdict(list)
    for (schema_name, table_name), table_meta in sorted(tables_meta.items()):
        table_key = (schema_name, table_name)
        tables_by_schema[schema_name].append(
            Table(
                id=table_ids[table_key],
                name=table_name,
                schema_id=make_schema_id(source_name, database_name, schema_name),
                columns=columns_models[table_key],
                primary_key=primary_keys.get(table_key),
                foreign_keys=tuple(foreign_keys_by_table.get(table_key, [])),
                description=table_meta.get("table_description"),
                ownership=_ownership(
                    None
                    if table_meta.get("table_owner") is None
                    else str(table_meta["table_owner"])
                ),
                technical_attributes={},
            )
        )

    schemas: list[Schema] = []
    for schema_name, schema_row in sorted(schemas_meta.items()):
        schemas.append(
            Schema(
                id=make_schema_id(source_name, database_name, schema_name),
                name=schema_name,
                database_id=database_id,
                tables=tuple(tables_by_schema.get(schema_name, [])),
                description=schema_row.get("description"),
                ownership=_ownership(
                    None if schema_row.get("owner_name") is None else str(schema_row["owner_name"])
                ),
            )
        )

    database = Database(
        id=database_id,
        name=database_name,
        datasource_id=data_source_id,
        schemas=tuple(schemas),
        description=database_row.get("description"),
        ownership=_ownership(
            None if database_row.get("owner_name") is None else str(database_row["owner_name"])
        ),
    )
    data_source = DataSource(
        id=data_source_id,
        name=source_name,
        system_type="postgresql",
        databases=(database,),
        description=None,
        ownership=None,
        technical_attributes={},
    )
    model = GovernanceModel(
        data_sources=(data_source,),
        relationships=tuple(relationships),
    )
    _validate_graph_integrity(model)
    return model


class PostgresMetadataScanner:
    """Discover technical metadata from a connected PostgreSQL database."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def scan(self) -> GovernanceModel:
        try:
            with (
                psycopg.connect(
                    host=self._settings.postgres_host,
                    port=self._settings.postgres_port,
                    dbname=self._settings.postgres_db,
                    user=self._settings.postgres_user,
                    password=self._settings.postgres_password,
                    connect_timeout=5,
                    row_factory=dict_row,
                ) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute("SET LOCAL statement_timeout = '10000ms'")

                cursor.execute(_SQL_DATABASE_METADATA)
                database_row = cursor.fetchone()
                if database_row is None:
                    raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")

                cursor.execute(_SQL_SCHEMAS_METADATA)
                schema_rows = list(cursor.fetchall())

                cursor.execute(_SQL_TABLES_COLUMNS_METADATA)
                table_column_rows = list(cursor.fetchall())

                cursor.execute(_SQL_PRIMARY_KEYS_METADATA)
                primary_key_rows = list(cursor.fetchall())

                cursor.execute(_SQL_FOREIGN_KEYS_METADATA)
                foreign_key_rows = list(cursor.fetchall())

            return build_governance_model(
                source_name=self._settings.postgres_source_name,
                database_row=database_row,
                schema_rows=schema_rows,
                table_column_rows=table_column_rows,
                primary_key_rows=primary_key_rows,
                foreign_key_rows=foreign_key_rows,
            )
        except MetadataDiscoveryError:
            raise
        except Exception:
            raise MetadataDiscoveryError("PostgreSQL metadata discovery failed") from None
