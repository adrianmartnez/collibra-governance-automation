"""Unit tests for GovernanceSnapshot persistence and integrity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.domain import (
    Column,
    Database,
    DataSource,
    GovernanceModel,
    PrimaryKey,
    Schema,
    Table,
    make_column_id,
    make_database_id,
    make_datasource_id,
    make_primary_key_id,
    make_schema_id,
    make_table_id,
)
from governance.snapshots import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    GovernanceSnapshot,
    SnapshotCompatibilityError,
    SnapshotIntegrityError,
    SnapshotIOError,
    load_snapshot,
    snapshot_to_json,
    write_snapshot,
)


def _sample_model() -> GovernanceModel:
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

    column = Column(
        id=column_id,
        name=column_name,
        data_type="integer",
        ordinal_position=1,
        nullable=False,
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
        system_type="postgresql",
        databases=(database,),
    )
    return GovernanceModel(data_sources=(data_source,), relationships=())


def test_snapshot_round_trip_and_byte_identity(tmp_path: Path) -> None:
    snapshot = GovernanceSnapshot.from_model(_sample_model())
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    write_snapshot(snapshot, path_a)
    write_snapshot(snapshot, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()

    loaded = load_snapshot(path_a)
    assert loaded.content_identity() == snapshot.content_identity()
    assert snapshot_to_json(loaded) == snapshot_to_json(snapshot)

    payload = json.loads(path_a.read_text(encoding="utf-8"))
    assert payload["snapshot_schema"] == SNAPSHOT_SCHEMA
    assert payload["snapshot_version"] == SNAPSHOT_VERSION
    assert "content_identity" in payload
    assert "password" not in path_a.read_text(encoding="utf-8").lower()


def test_snapshot_integrity_mismatch(tmp_path: Path) -> None:
    snapshot = GovernanceSnapshot.from_model(_sample_model())
    path = tmp_path / "snap.json"
    write_snapshot(snapshot, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["content_identity"]["digest"] = "0" * 64
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SnapshotIntegrityError):
        load_snapshot(path)


def test_unsupported_snapshot_version(tmp_path: Path) -> None:
    snapshot = GovernanceSnapshot.from_model(_sample_model())
    path = tmp_path / "snap.json"
    write_snapshot(snapshot, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["snapshot_version"] = "99"
    # Keep a plausible identity so version check fails first.
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SnapshotCompatibilityError):
        load_snapshot(path)


def test_write_snapshot_rejects_directory(tmp_path: Path) -> None:
    snapshot = GovernanceSnapshot.from_model(_sample_model())
    with pytest.raises(SnapshotIOError):
        write_snapshot(snapshot, tmp_path)
