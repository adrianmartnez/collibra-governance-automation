"""Vendor-neutral governance domain model.

This module must remain independent of database drivers and catalog API clients.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _require_non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value


def make_datasource_id(name: str) -> str:
    return f"ds:{_require_non_empty(name, 'name')}"


def make_database_id(datasource_name: str, database_name: str) -> str:
    return (
        f"db:{_require_non_empty(datasource_name, 'datasource_name')}/"
        f"{_require_non_empty(database_name, 'database_name')}"
    )


def make_schema_id(datasource_name: str, database_name: str, schema_name: str) -> str:
    return (
        f"sch:{_require_non_empty(datasource_name, 'datasource_name')}/"
        f"{_require_non_empty(database_name, 'database_name')}/"
        f"{_require_non_empty(schema_name, 'schema_name')}"
    )


def make_table_id(
    datasource_name: str,
    database_name: str,
    schema_name: str,
    table_name: str,
) -> str:
    return (
        f"tbl:{_require_non_empty(datasource_name, 'datasource_name')}/"
        f"{_require_non_empty(database_name, 'database_name')}/"
        f"{_require_non_empty(schema_name, 'schema_name')}/"
        f"{_require_non_empty(table_name, 'table_name')}"
    )


def make_column_id(
    datasource_name: str,
    database_name: str,
    schema_name: str,
    table_name: str,
    column_name: str,
) -> str:
    return (
        f"col:{_require_non_empty(datasource_name, 'datasource_name')}/"
        f"{_require_non_empty(database_name, 'database_name')}/"
        f"{_require_non_empty(schema_name, 'schema_name')}/"
        f"{_require_non_empty(table_name, 'table_name')}/"
        f"{_require_non_empty(column_name, 'column_name')}"
    )


def make_primary_key_id(table_id: str, constraint_name: str) -> str:
    return (
        f"pk:{_require_non_empty(table_id, 'table_id')}/"
        f"{_require_non_empty(constraint_name, 'constraint_name')}"
    )


def make_foreign_key_id(table_id: str, constraint_name: str) -> str:
    return (
        f"fk:{_require_non_empty(table_id, 'table_id')}/"
        f"{_require_non_empty(constraint_name, 'constraint_name')}"
    )


def make_relationship_id(name: str) -> str:
    return f"rel:{_require_non_empty(name, 'name')}"


@dataclass(frozen=True, slots=True)
class Ownership:
    """Ownership metadata for a governed object."""

    owner_name: str
    owner_type: str = "role"

    def __post_init__(self) -> None:
        _require_non_empty(self.owner_name, "owner_name")
        _require_non_empty(self.owner_type, "owner_type")

    def to_dict(self) -> dict[str, Any]:
        return {"owner_name": self.owner_name, "owner_type": self.owner_type}


@dataclass(frozen=True, slots=True)
class Column:
    id: str
    name: str
    data_type: str
    ordinal_position: int
    nullable: bool = True
    description: str | None = None
    technical_attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.name, "name")
        _require_non_empty(self.data_type, "data_type")
        if self.ordinal_position < 1:
            raise ValueError("ordinal_position must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "data_type": self.data_type,
            "ordinal_position": self.ordinal_position,
            "nullable": self.nullable,
            "description": self.description,
            "technical_attributes": dict(sorted(self.technical_attributes.items())),
        }


@dataclass(frozen=True, slots=True)
class PrimaryKey:
    id: str
    name: str
    table_id: str
    column_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.name, "name")
        _require_non_empty(self.table_id, "table_id")
        if not self.column_ids:
            raise ValueError("column_ids must not be empty")
        for column_id in self.column_ids:
            _require_non_empty(column_id, "column_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "table_id": self.table_id,
            "column_ids": list(self.column_ids),
        }


@dataclass(frozen=True, slots=True)
class ForeignKey:
    id: str
    name: str
    table_id: str
    column_ids: tuple[str, ...]
    referenced_table_id: str
    referenced_column_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.name, "name")
        _require_non_empty(self.table_id, "table_id")
        _require_non_empty(self.referenced_table_id, "referenced_table_id")
        if not self.column_ids:
            raise ValueError("column_ids must not be empty")
        if not self.referenced_column_ids:
            raise ValueError("referenced_column_ids must not be empty")
        if len(self.column_ids) != len(self.referenced_column_ids):
            raise ValueError("column_ids and referenced_column_ids must have equal length")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "table_id": self.table_id,
            "column_ids": list(self.column_ids),
            "referenced_table_id": self.referenced_table_id,
            "referenced_column_ids": list(self.referenced_column_ids),
        }


@dataclass(frozen=True, slots=True)
class Relationship:
    id: str
    name: str
    from_table_id: str
    to_table_id: str
    foreign_key_id: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.name, "name")
        _require_non_empty(self.from_table_id, "from_table_id")
        _require_non_empty(self.to_table_id, "to_table_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "from_table_id": self.from_table_id,
            "to_table_id": self.to_table_id,
            "foreign_key_id": self.foreign_key_id,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class Table:
    id: str
    name: str
    schema_id: str
    columns: tuple[Column, ...]
    primary_key: PrimaryKey | None = None
    foreign_keys: tuple[ForeignKey, ...] = ()
    description: str | None = None
    ownership: Ownership | None = None
    technical_attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.name, "name")
        _require_non_empty(self.schema_id, "schema_id")
        if not self.columns:
            raise ValueError("columns must not be empty")
        object.__setattr__(
            self,
            "columns",
            tuple(sorted(self.columns, key=lambda column: (column.ordinal_position, column.id))),
        )
        object.__setattr__(
            self,
            "foreign_keys",
            tuple(sorted(self.foreign_keys, key=lambda foreign_key: foreign_key.id)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "schema_id": self.schema_id,
            "columns": [column.to_dict() for column in self.columns],
            "primary_key": self.primary_key.to_dict() if self.primary_key else None,
            "foreign_keys": [foreign_key.to_dict() for foreign_key in self.foreign_keys],
            "description": self.description,
            "ownership": self.ownership.to_dict() if self.ownership else None,
            "technical_attributes": dict(sorted(self.technical_attributes.items())),
        }


@dataclass(frozen=True, slots=True)
class Schema:
    id: str
    name: str
    database_id: str
    tables: tuple[Table, ...] = ()
    description: str | None = None
    ownership: Ownership | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.name, "name")
        _require_non_empty(self.database_id, "database_id")
        object.__setattr__(self, "tables", tuple(sorted(self.tables, key=lambda table: table.id)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "database_id": self.database_id,
            "tables": [table.to_dict() for table in self.tables],
            "description": self.description,
            "ownership": self.ownership.to_dict() if self.ownership else None,
        }


@dataclass(frozen=True, slots=True)
class Database:
    id: str
    name: str
    datasource_id: str
    schemas: tuple[Schema, ...] = ()
    description: str | None = None
    ownership: Ownership | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.name, "name")
        _require_non_empty(self.datasource_id, "datasource_id")
        object.__setattr__(
            self,
            "schemas",
            tuple(sorted(self.schemas, key=lambda schema: schema.id)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "datasource_id": self.datasource_id,
            "schemas": [schema.to_dict() for schema in self.schemas],
            "description": self.description,
            "ownership": self.ownership.to_dict() if self.ownership else None,
        }


@dataclass(frozen=True, slots=True)
class DataSource:
    id: str
    name: str
    system_type: str
    databases: tuple[Database, ...] = ()
    description: str | None = None
    ownership: Ownership | None = None
    technical_attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.name, "name")
        _require_non_empty(self.system_type, "system_type")
        object.__setattr__(
            self,
            "databases",
            tuple(sorted(self.databases, key=lambda database: database.id)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "system_type": self.system_type,
            "databases": [database.to_dict() for database in self.databases],
            "description": self.description,
            "ownership": self.ownership.to_dict() if self.ownership else None,
            "technical_attributes": dict(sorted(self.technical_attributes.items())),
        }


@dataclass(frozen=True, slots=True)
class GovernanceModel:
    """Root container for a vendor-neutral metadata graph."""

    data_sources: tuple[DataSource, ...]
    relationships: tuple[Relationship, ...] = ()

    def __post_init__(self) -> None:
        if not self.data_sources:
            raise ValueError("data_sources must not be empty")
        object.__setattr__(
            self,
            "data_sources",
            tuple(sorted(self.data_sources, key=lambda data_source: data_source.id)),
        )
        object.__setattr__(
            self,
            "relationships",
            tuple(sorted(self.relationships, key=lambda relationship: relationship.id)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_sources": [data_source.to_dict() for data_source in self.data_sources],
            "relationships": [relationship.to_dict() for relationship in self.relationships],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
