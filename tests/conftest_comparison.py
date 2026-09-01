"""Pytest fixtures and helpers for comparison tests."""

from __future__ import annotations

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
from governance.snapshots import GovernanceSnapshot


def build_sample_model(
    *,
    source_name: str = "governance-demo",
    database_name: str = "governance_demo",
    system_type: str = "postgresql",
    schema_name: str = "sales",
    table_name: str = "orders",
    column_name: str = "id",
    ref_table_name: str = "customers",
    ref_column_name: str = "id",
    description: str | None = None,
    column_description: str | None = None,
    ownership: Ownership | None = None,
    technical_attributes: dict | None = None,
    include_fk: bool = True,
    extra_column: Column | None = None,
) -> GovernanceModel:
    datasource_id = make_datasource_id(source_name)
    database_id = make_database_id(source_name, database_name)
    schema_id = make_schema_id(source_name, database_name, schema_name)
    table_id = make_table_id(source_name, database_name, schema_name, table_name)
    column_id = make_column_id(source_name, database_name, schema_name, table_name, column_name)
    ref_table_id = make_table_id(source_name, database_name, schema_name, ref_table_name)
    ref_column_id = make_column_id(
        source_name, database_name, schema_name, ref_table_name, ref_column_name
    )
    pk_id = make_primary_key_id(table_id, f"{table_name}_pkey")
    ref_pk_id = make_primary_key_id(ref_table_id, f"{ref_table_name}_pkey")

    column = Column(
        id=column_id,
        name=column_name,
        data_type="integer",
        ordinal_position=1,
        nullable=False,
        description=column_description,
        technical_attributes=dict(technical_attributes or {}),
    )
    columns: list[Column] = [column]
    if extra_column is not None:
        columns.append(extra_column)

    foreign_keys: tuple[ForeignKey, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    if include_fk:
        fk_id = make_foreign_key_id(table_id, f"{table_name}_{column_name}_fkey")
        foreign_keys = (
            ForeignKey(
                id=fk_id,
                name=f"{table_name}_{column_name}_fkey",
                table_id=table_id,
                column_ids=(column_id,),
                referenced_table_id=ref_table_id,
                referenced_column_ids=(ref_column_id,),
            ),
        )
        relationships = (
            Relationship(
                id=make_relationship_id(fk_id),
                name=f"{table_name}_{column_name}_fkey",
                from_table_id=table_id,
                to_table_id=ref_table_id,
                foreign_key_id=fk_id,
            ),
        )

    table = Table(
        id=table_id,
        name=table_name,
        schema_id=schema_id,
        columns=tuple(columns),
        primary_key=PrimaryKey(
            id=pk_id,
            name=f"{table_name}_pkey",
            table_id=table_id,
            column_ids=(column_id,),
        ),
        foreign_keys=foreign_keys,
        description=description,
        ownership=ownership,
    )
    ref_column = Column(
        id=ref_column_id,
        name=ref_column_name,
        data_type="integer",
        ordinal_position=1,
        nullable=False,
    )
    ref_table = Table(
        id=ref_table_id,
        name=ref_table_name,
        schema_id=schema_id,
        columns=(ref_column,),
        primary_key=PrimaryKey(
            id=ref_pk_id,
            name=f"{ref_table_name}_pkey",
            table_id=ref_table_id,
            column_ids=(ref_column_id,),
        ),
    )
    schema = Schema(
        id=schema_id,
        name=schema_name,
        database_id=database_id,
        tables=(table, ref_table),
        ownership=ownership,
    )
    database = Database(
        id=database_id,
        name=database_name,
        datasource_id=datasource_id,
        schemas=(schema,),
        ownership=ownership,
    )
    data_source = DataSource(
        id=datasource_id,
        name=source_name,
        system_type=system_type,
        databases=(database,),
        description=description,
        ownership=ownership,
        technical_attributes=dict(technical_attributes or {}),
    )
    return GovernanceModel(data_sources=(data_source,), relationships=relationships)


def build_snapshot(**kwargs) -> GovernanceSnapshot:
    return GovernanceSnapshot.from_model(build_sample_model(**kwargs))
