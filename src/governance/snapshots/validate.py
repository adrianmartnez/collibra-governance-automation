"""Semantic validation and strict parsing for governance-snapshot v1 artifacts."""

from __future__ import annotations

from typing import Any

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
)
from governance.exporters.inventory import SCANNER_CONTRACT_VERSION
from governance.identity.json_values import normalize_json_value, validate_json_value
from governance.snapshots.errors import SnapshotCompatibilityError
from governance.snapshots.models import GovernanceSnapshot


def _payload_error(message: str, *, path: str = "/governance") -> SnapshotCompatibilityError:
    return SnapshotCompatibilityError(
        message,
        code="invalid_snapshot_payload",
        path=path,
    )


def _require_str(value: Any, *, path: str) -> str:
    if not isinstance(value, str):
        raise _payload_error("value must be a string", path=path)
    return value


def _require_non_empty_str(value: Any, *, path: str) -> str:
    text = _require_str(value, path=path)
    if text == "":
        raise _payload_error("string must be non-empty", path=path)
    return text


def _require_int(value: Any, *, path: str) -> int:
    # bool is a subclass of int — reject explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _payload_error("value must be an integer", path=path)
    return value


def _require_bool(value: Any, *, path: str) -> bool:
    if not isinstance(value, bool):
        raise _payload_error("value must be a boolean", path=path)
    return value


def _require_mapping(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _payload_error("value must be a mapping", path=path)
    return value


def _require_list(value: Any, *, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise _payload_error("value must be an array", path=path)
    return value


def _description_from_value(value: Any, *, path: str) -> Any:
    try:
        validate_json_value(value)
    except (TypeError, ValueError, RecursionError) as exc:
        raise _payload_error("description must be finite JSON", path=path) from exc
    return normalize_json_value(value)


def _ownership_from_dict(raw: Any, *, path: str) -> Ownership | None:
    if raw is None:
        return None
    mapping = _require_mapping(raw, path=path)
    return Ownership(
        owner_name=_require_non_empty_str(mapping.get("owner_name"), path=f"{path}/owner_name"),
        owner_type=_require_non_empty_str(
            mapping.get("owner_type", "role"),
            path=f"{path}/owner_type",
        ),
    )


def _technical_attributes_from_dict(raw: Any, *, path: str) -> dict[str, Any]:
    mapping = _require_mapping(raw, path=path)
    for key in mapping:
        if not isinstance(key, str):
            raise _payload_error("technical_attributes keys must be strings", path=path)
    try:
        validate_json_value(mapping)
    except (TypeError, ValueError, RecursionError) as exc:
        raise _payload_error(
            "technical_attributes must contain finite JSON values",
            path=path,
        ) from exc
    return dict(mapping)


def _register_id(seen: dict[str, str], object_id: str, *, path: str) -> None:
    if object_id in seen:
        raise _payload_error(f"duplicate snapshot object id: {object_id}", path=path)
    seen[object_id] = object_id


def _column_from_dict(raw: Any, *, path: str, seen_ids: dict[str, str]) -> Column:
    mapping = _require_mapping(raw, path=path)
    column_id = _require_non_empty_str(mapping.get("id"), path=f"{path}/id")
    _register_id(seen_ids, column_id, path=f"{path}/id")
    return Column(
        id=column_id,
        name=_require_non_empty_str(mapping.get("name"), path=f"{path}/name"),
        data_type=_require_non_empty_str(mapping.get("data_type"), path=f"{path}/data_type"),
        ordinal_position=_require_int(
            mapping.get("ordinal_position"),
            path=f"{path}/ordinal_position",
        ),
        nullable=_require_bool(mapping.get("nullable"), path=f"{path}/nullable"),
        description=_description_from_value(mapping.get("description"), path=f"{path}/description"),
        technical_attributes=_technical_attributes_from_dict(
            mapping.get("technical_attributes", {}),
            path=f"{path}/technical_attributes",
        ),
    )


def _primary_key_from_dict(
    raw: Any,
    *,
    path: str,
    table_id: str,
    column_ids: set[str],
    seen_ids: dict[str, str],
) -> PrimaryKey:
    mapping = _require_mapping(raw, path=path)
    pk_id = _require_non_empty_str(mapping.get("id"), path=f"{path}/id")
    _register_id(seen_ids, pk_id, path=f"{path}/id")
    pk_table_id = _require_non_empty_str(mapping.get("table_id"), path=f"{path}/table_id")
    if pk_table_id != table_id:
        raise _payload_error(
            "primary_key.table_id does not resolve to containing table",
            path=f"{path}/table_id",
        )
    raw_column_ids = _require_list(mapping.get("column_ids"), path=f"{path}/column_ids")
    parsed_column_ids: list[str] = []
    for index, item in enumerate(raw_column_ids):
        column_id = _require_non_empty_str(item, path=f"{path}/column_ids/{index}")
        if column_id not in column_ids:
            raise _payload_error(
                "primary_key column_id does not resolve on containing table",
                path=f"{path}/column_ids/{index}",
            )
        parsed_column_ids.append(column_id)
    return PrimaryKey(
        id=pk_id,
        name=_require_non_empty_str(mapping.get("name"), path=f"{path}/name"),
        table_id=pk_table_id,
        column_ids=tuple(parsed_column_ids),
    )


def _foreign_key_from_dict(
    raw: Any,
    *,
    path: str,
    table_id: str,
    column_ids: set[str],
    table_ids: set[str],
    columns_by_table: dict[str, set[str]],
    seen_ids: dict[str, str],
) -> ForeignKey:
    mapping = _require_mapping(raw, path=path)
    fk_id = _require_non_empty_str(mapping.get("id"), path=f"{path}/id")
    _register_id(seen_ids, fk_id, path=f"{path}/id")
    fk_table_id = _require_non_empty_str(mapping.get("table_id"), path=f"{path}/table_id")
    if fk_table_id != table_id:
        raise _payload_error(
            "foreign_key.table_id does not resolve to containing table",
            path=f"{path}/table_id",
        )
    raw_column_ids = _require_list(mapping.get("column_ids"), path=f"{path}/column_ids")
    parsed_column_ids: list[str] = []
    for index, item in enumerate(raw_column_ids):
        column_id = _require_non_empty_str(item, path=f"{path}/column_ids/{index}")
        if column_id not in column_ids:
            raise _payload_error(
                "foreign_key column_id does not resolve on containing table",
                path=f"{path}/column_ids/{index}",
            )
        parsed_column_ids.append(column_id)

    referenced_table_id = _require_non_empty_str(
        mapping.get("referenced_table_id"),
        path=f"{path}/referenced_table_id",
    )
    if referenced_table_id not in table_ids:
        raise _payload_error(
            "referenced_table_id does not resolve to a table",
            path=f"{path}/referenced_table_id",
        )
    raw_ref_column_ids = _require_list(
        mapping.get("referenced_column_ids"),
        path=f"{path}/referenced_column_ids",
    )
    parsed_ref_column_ids: list[str] = []
    ref_columns = columns_by_table.get(referenced_table_id, set())
    for index, item in enumerate(raw_ref_column_ids):
        column_id = _require_non_empty_str(
            item,
            path=f"{path}/referenced_column_ids/{index}",
        )
        if column_id not in ref_columns:
            raise _payload_error(
                "referenced_column_id does not resolve on referenced table",
                path=f"{path}/referenced_column_ids/{index}",
            )
        parsed_ref_column_ids.append(column_id)

    if len(parsed_column_ids) != len(parsed_ref_column_ids):
        raise _payload_error(
            "column_ids and referenced_column_ids length mismatch",
            path=path,
        )

    return ForeignKey(
        id=fk_id,
        name=_require_non_empty_str(mapping.get("name"), path=f"{path}/name"),
        table_id=fk_table_id,
        column_ids=tuple(parsed_column_ids),
        referenced_table_id=referenced_table_id,
        referenced_column_ids=tuple(parsed_ref_column_ids),
    )


def validate_and_parse_snapshot_payload(payload: dict[str, Any]) -> GovernanceSnapshot:
    """Strictly parse a schema-validated snapshot payload into GovernanceSnapshot."""
    source = _require_mapping(payload.get("source"), path="/source")
    scan = _require_mapping(payload.get("scan"), path="/scan")
    governance = _require_mapping(payload.get("governance"), path="/governance")

    source_name = _require_non_empty_str(source.get("name"), path="/source/name")
    database_name = _require_non_empty_str(source.get("database"), path="/source/database")
    system_type = _require_non_empty_str(source.get("system_type"), path="/source/system_type")

    scanner = _require_non_empty_str(scan.get("scanner"), path="/scan/scanner")
    scanner_contract_version = _require_non_empty_str(
        scan.get("scanner_contract_version"),
        path="/scan/scanner_contract_version",
    )
    if scanner_contract_version != SCANNER_CONTRACT_VERSION:
        raise _payload_error(
            f"scanner_contract_version must be {SCANNER_CONTRACT_VERSION!r}",
            path="/scan/scanner_contract_version",
        )

    raw_data_sources = _require_list(
        governance.get("data_sources"),
        path="/governance/data_sources",
    )
    if len(raw_data_sources) != 1:
        raise _payload_error(
            "snapshot must contain exactly one data_source",
            path="/governance/data_sources",
        )

    seen_ids: dict[str, str] = {}
    table_ids: set[str] = set()
    columns_by_table: dict[str, set[str]] = {}
    foreign_keys_by_id: dict[str, ForeignKey] = {}
    pending_fks: list[tuple[str, Any, str]] = []

    ds_raw = _require_mapping(raw_data_sources[0], path="/governance/data_sources/0")
    ds_id = _require_non_empty_str(ds_raw.get("id"), path="/governance/data_sources/0/id")
    _register_id(seen_ids, ds_id, path="/governance/data_sources/0/id")
    ds_name = _require_non_empty_str(ds_raw.get("name"), path="/governance/data_sources/0/name")
    ds_system_type = _require_non_empty_str(
        ds_raw.get("system_type"),
        path="/governance/data_sources/0/system_type",
    )

    raw_databases = _require_list(
        ds_raw.get("databases"),
        path="/governance/data_sources/0/databases",
    )
    if len(raw_databases) != 1:
        raise _payload_error(
            "snapshot must contain exactly one database",
            path="/governance/data_sources/0/databases",
        )

    db_path = "/governance/data_sources/0/databases/0"
    db_raw = _require_mapping(raw_databases[0], path=db_path)
    db_id = _require_non_empty_str(db_raw.get("id"), path=f"{db_path}/id")
    _register_id(seen_ids, db_id, path=f"{db_path}/id")
    db_name = _require_non_empty_str(db_raw.get("name"), path=f"{db_path}/name")
    db_datasource_id = _require_non_empty_str(
        db_raw.get("datasource_id"),
        path=f"{db_path}/datasource_id",
    )
    if db_datasource_id != ds_id:
        raise _payload_error(
            "database.datasource_id does not resolve to data_source",
            path=f"{db_path}/datasource_id",
        )

    schemas_raw = _require_list(db_raw.get("schemas"), path=f"{db_path}/schemas")
    provisional_schemas: list[Schema] = []
    for schema_index, schema_item in enumerate(schemas_raw):
        schema_path = f"{db_path}/schemas/{schema_index}"
        schema_mapping = _require_mapping(schema_item, path=schema_path)
        schema_id = _require_non_empty_str(schema_mapping.get("id"), path=f"{schema_path}/id")
        _register_id(seen_ids, schema_id, path=f"{schema_path}/id")
        resolved_database_id = _require_non_empty_str(
            schema_mapping.get("database_id"),
            path=f"{schema_path}/database_id",
        )
        if resolved_database_id != db_id:
            raise _payload_error(
                "schema.database_id does not resolve to database",
                path=f"{schema_path}/database_id",
            )

        tables_raw = _require_list(schema_mapping.get("tables"), path=f"{schema_path}/tables")
        tables: list[Table] = []
        for table_index, table_item in enumerate(tables_raw):
            table_path = f"{schema_path}/tables/{table_index}"
            table_mapping = _require_mapping(table_item, path=table_path)
            table_id = _require_non_empty_str(table_mapping.get("id"), path=f"{table_path}/id")
            _register_id(seen_ids, table_id, path=f"{table_path}/id")
            table_ids.add(table_id)
            resolved_schema_id = _require_non_empty_str(
                table_mapping.get("schema_id"),
                path=f"{table_path}/schema_id",
            )
            if resolved_schema_id != schema_id:
                raise _payload_error(
                    "table.schema_id does not resolve to containing schema",
                    path=f"{table_path}/schema_id",
                )

            columns_raw = _require_list(table_mapping.get("columns"), path=f"{table_path}/columns")
            columns = tuple(
                _column_from_dict(
                    column_item,
                    path=f"{table_path}/columns/{column_index}",
                    seen_ids=seen_ids,
                )
                for column_index, column_item in enumerate(columns_raw)
            )
            column_id_set = {column.id for column in columns}
            columns_by_table[table_id] = column_id_set

            pk_raw = table_mapping.get("primary_key")
            primary_key = None
            if pk_raw is not None:
                primary_key = _primary_key_from_dict(
                    pk_raw,
                    path=f"{table_path}/primary_key",
                    table_id=table_id,
                    column_ids=column_id_set,
                    seen_ids=seen_ids,
                )

            fks_raw = _require_list(
                table_mapping.get("foreign_keys"),
                path=f"{table_path}/foreign_keys",
            )
            for fk_index, fk_item in enumerate(fks_raw):
                pending_fks.append((table_id, fk_item, f"{table_path}/foreign_keys/{fk_index}"))

            tables.append(
                Table(
                    id=table_id,
                    name=_require_non_empty_str(
                        table_mapping.get("name"),
                        path=f"{table_path}/name",
                    ),
                    schema_id=resolved_schema_id,
                    columns=columns,
                    primary_key=primary_key,
                    foreign_keys=(),
                    description=_description_from_value(
                        table_mapping.get("description"),
                        path=f"{table_path}/description",
                    ),
                    ownership=_ownership_from_dict(
                        table_mapping.get("ownership"),
                        path=f"{table_path}/ownership",
                    ),
                    technical_attributes=_technical_attributes_from_dict(
                        table_mapping.get("technical_attributes", {}),
                        path=f"{table_path}/technical_attributes",
                    ),
                )
            )

        provisional_schemas.append(
            Schema(
                id=schema_id,
                name=_require_non_empty_str(schema_mapping.get("name"), path=f"{schema_path}/name"),
                database_id=resolved_database_id,
                tables=tuple(tables),
                description=_description_from_value(
                    schema_mapping.get("description"),
                    path=f"{schema_path}/description",
                ),
                ownership=_ownership_from_dict(
                    schema_mapping.get("ownership"),
                    path=f"{schema_path}/ownership",
                ),
            )
        )

    fks_by_table: dict[str, list[ForeignKey]] = {table_id: [] for table_id in table_ids}
    for table_id, fk_raw, fk_path in pending_fks:
        foreign_key = _foreign_key_from_dict(
            fk_raw,
            path=fk_path,
            table_id=table_id,
            column_ids=columns_by_table[table_id],
            table_ids=table_ids,
            columns_by_table=columns_by_table,
            seen_ids=seen_ids,
        )
        foreign_keys_by_id[foreign_key.id] = foreign_key
        fks_by_table[table_id].append(foreign_key)

    rebuilt_schemas: list[Schema] = []
    for schema in provisional_schemas:
        rebuilt_tables = [
            Table(
                id=table.id,
                name=table.name,
                schema_id=table.schema_id,
                columns=table.columns,
                primary_key=table.primary_key,
                foreign_keys=tuple(fks_by_table.get(table.id, [])),
                description=table.description,
                ownership=table.ownership,
                technical_attributes=dict(table.technical_attributes),
            )
            for table in schema.tables
        ]
        rebuilt_schemas.append(
            Schema(
                id=schema.id,
                name=schema.name,
                database_id=schema.database_id,
                tables=tuple(rebuilt_tables),
                description=schema.description,
                ownership=schema.ownership,
            )
        )

    database = Database(
        id=db_id,
        name=db_name,
        datasource_id=db_datasource_id,
        schemas=tuple(rebuilt_schemas),
        description=_description_from_value(
            db_raw.get("description"),
            path=f"{db_path}/description",
        ),
        ownership=_ownership_from_dict(db_raw.get("ownership"), path=f"{db_path}/ownership"),
    )
    data_source = DataSource(
        id=ds_id,
        name=ds_name,
        system_type=ds_system_type,
        databases=(database,),
        description=_description_from_value(
            ds_raw.get("description"),
            path="/governance/data_sources/0/description",
        ),
        ownership=_ownership_from_dict(
            ds_raw.get("ownership"),
            path="/governance/data_sources/0/ownership",
        ),
        technical_attributes=_technical_attributes_from_dict(
            ds_raw.get("technical_attributes", {}),
            path="/governance/data_sources/0/technical_attributes",
        ),
    )

    if source_name != data_source.name:
        raise _payload_error(
            "envelope source name does not match model data_source.name",
            path="/source/name",
        )
    if database_name != database.name:
        raise _payload_error(
            "envelope database name does not match model database.name",
            path="/source/database",
        )
    if system_type != data_source.system_type:
        raise _payload_error(
            "envelope system_type does not match model data_source.system_type",
            path="/source/system_type",
        )

    relationships_raw = _require_list(
        governance.get("relationships"),
        path="/governance/relationships",
    )
    relationships: list[Relationship] = []
    for index, item in enumerate(relationships_raw):
        rel_path = f"/governance/relationships/{index}"
        rel = _require_mapping(item, path=rel_path)
        rel_id = _require_non_empty_str(rel.get("id"), path=f"{rel_path}/id")
        _register_id(seen_ids, rel_id, path=f"{rel_path}/id")
        from_table_id = _require_non_empty_str(
            rel.get("from_table_id"),
            path=f"{rel_path}/from_table_id",
        )
        to_table_id = _require_non_empty_str(
            rel.get("to_table_id"),
            path=f"{rel_path}/to_table_id",
        )
        if from_table_id not in table_ids:
            raise _payload_error(
                "relationship.from_table_id does not resolve",
                path=f"{rel_path}/from_table_id",
            )
        if to_table_id not in table_ids:
            raise _payload_error(
                "relationship.to_table_id does not resolve to a table",
                path=f"{rel_path}/to_table_id",
            )
        foreign_key_id = rel.get("foreign_key_id")
        if foreign_key_id is not None:
            foreign_key_id = _require_non_empty_str(
                foreign_key_id,
                path=f"{rel_path}/foreign_key_id",
            )
            fk = foreign_keys_by_id.get(foreign_key_id)
            if fk is None:
                raise _payload_error(
                    "relationship.foreign_key_id does not resolve to a foreign_key",
                    path=f"{rel_path}/foreign_key_id",
                )
            if fk.table_id != from_table_id:
                raise _payload_error(
                    "foreign_key source table does not match relationship.from_table_id",
                    path=f"{rel_path}/foreign_key_id",
                )
            if fk.referenced_table_id != to_table_id:
                raise _payload_error(
                    "foreign_key referenced table does not match relationship.to_table_id",
                    path=f"{rel_path}/foreign_key_id",
                )
        try:
            relationships.append(
                Relationship(
                    id=rel_id,
                    name=_require_non_empty_str(rel.get("name"), path=f"{rel_path}/name"),
                    from_table_id=from_table_id,
                    to_table_id=to_table_id,
                    foreign_key_id=foreign_key_id,
                    description=_description_from_value(
                        rel.get("description"),
                        path=f"{rel_path}/description",
                    ),
                )
            )
        except ValueError as exc:
            raise _payload_error(str(exc), path=rel_path) from exc

    try:
        model = GovernanceModel(
            data_sources=(data_source,),
            relationships=tuple(relationships),
        )
    except ValueError as exc:
        raise _payload_error(str(exc), path="/governance") from exc

    return GovernanceSnapshot(
        model=model,
        source_name=source_name,
        database_name=database_name,
        system_type=system_type,
        scanner=scanner,
        scanner_contract_version=scanner_contract_version,
    )


__all__ = ["validate_and_parse_snapshot_payload"]
