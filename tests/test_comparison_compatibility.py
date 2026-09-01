"""Compatibility and reference-closure tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest_comparison import build_snapshot
from governance.cli import main
from governance.comparison import ComparisonError, build_comparison_result
from governance.comparison.errors import (
    CODE_INVALID_SNAPSHOT_REFERENCES,
    CODE_SCANNER_MISMATCH,
    CODE_SYSTEM_TYPE_MISMATCH,
)
from governance.domain import ForeignKey
from governance.snapshots import GovernanceSnapshot, write_snapshot
from governance.snapshots.models import GovernanceSnapshot as GS


def test_system_type_mismatch() -> None:
    baseline = build_snapshot(system_type="postgresql")
    # Rebuild candidate with different system_type while keeping shape
    candidate_model = build_snapshot(system_type="postgresql").model
    ds = candidate_model.data_sources[0]
    from governance.domain import DataSource, GovernanceModel

    alt = GovernanceModel(
        data_sources=(
            DataSource(
                id=ds.id,
                name=ds.name,
                system_type="otherdb",
                databases=ds.databases,
                description=ds.description,
                ownership=ds.ownership,
                technical_attributes=ds.technical_attributes,
            ),
        ),
        relationships=candidate_model.relationships,
    )
    candidate = GovernanceSnapshot.from_model(alt)
    with pytest.raises(ComparisonError) as exc_info:
        build_comparison_result(baseline, candidate)
    assert any(e.code == CODE_SYSTEM_TYPE_MISMATCH for e in exc_info.value.errors)


def test_scanner_mismatch() -> None:
    baseline = build_snapshot()
    candidate = GS(
        model=baseline.model,
        source_name=baseline.source_name,
        database_name=baseline.database_name,
        system_type=baseline.system_type,
        scanner="other-scanner",
        scanner_contract_version=baseline.scanner_contract_version,
    )
    with pytest.raises(ComparisonError) as exc_info:
        build_comparison_result(baseline, candidate)
    assert any(e.code == CODE_SCANNER_MISMATCH for e in exc_info.value.errors)


def test_fk_name_with_slash_dangling_ref_diagnostic_path() -> None:
    snap = build_snapshot()
    ds = snap.model.data_sources[0]
    db = ds.databases[0]
    schema = db.schemas[0]
    table = next(t for t in schema.tables if t.name == "orders")
    fk = table.foreign_keys[0]
    broken_fk = ForeignKey(
        id=fk.id,
        name="a/b",
        table_id=fk.table_id,
        column_ids=fk.column_ids,
        referenced_table_id="tbl:missing",
        referenced_column_ids=fk.referenced_column_ids,
    )
    from governance.domain import Database, DataSource, GovernanceModel, Schema, Table

    new_table = Table(
        id=table.id,
        name=table.name,
        schema_id=table.schema_id,
        columns=table.columns,
        primary_key=table.primary_key,
        foreign_keys=(broken_fk,),
        description=table.description,
        ownership=table.ownership,
        technical_attributes=table.technical_attributes,
    )
    other = next(t for t in schema.tables if t.name != "orders")
    new_schema = Schema(
        id=schema.id,
        name=schema.name,
        database_id=schema.database_id,
        tables=(new_table, other),
        description=schema.description,
        ownership=schema.ownership,
    )
    new_db = Database(
        id=db.id,
        name=db.name,
        datasource_id=db.datasource_id,
        schemas=(new_schema,),
        description=db.description,
        ownership=db.ownership,
    )
    new_ds = DataSource(
        id=ds.id,
        name=ds.name,
        system_type=ds.system_type,
        databases=(new_db,),
        description=ds.description,
        ownership=ds.ownership,
        technical_attributes=ds.technical_attributes,
    )
    model = GovernanceModel(data_sources=(new_ds,), relationships=())
    broken = GovernanceSnapshot.from_model(model)
    with pytest.raises(ComparisonError) as exc_info:
        build_comparison_result(broken, broken)
    ref_errors = [
        e
        for e in exc_info.value.errors
        if e.code == CODE_INVALID_SNAPSHOT_REFERENCES and "referenced_table_id" in e.path
    ]
    assert ref_errors
    from governance.comparison.projection import _diagnostic_path

    expected = _diagnostic_path(
        "baseline", "governance", "foreign_keys", "a/b", "referenced_table_id"
    )
    assert ref_errors[0].path == expected


def test_relationship_name_with_tilde_diagnostic_path() -> None:
    snap = build_snapshot()
    ds = snap.model.data_sources[0]
    rel = snap.model.relationships[0]
    from governance.domain import GovernanceModel, Relationship

    broken_rel = Relationship(
        id=rel.id,
        name="a~b",
        from_table_id="tbl:missing",
        to_table_id=rel.to_table_id,
        foreign_key_id=rel.foreign_key_id,
        description=rel.description,
    )
    model = GovernanceModel(
        data_sources=(ds,),
        relationships=(broken_rel,),
    )
    broken = GovernanceSnapshot.from_model(model)
    with pytest.raises(ComparisonError) as exc_info:
        build_comparison_result(broken, broken)
    rel_errors = [
        e
        for e in exc_info.value.errors
        if e.code == CODE_INVALID_SNAPSHOT_REFERENCES and "relationships" in e.path
    ]
    assert rel_errors
    from governance.comparison.projection import _diagnostic_path

    expected = _diagnostic_path("baseline", "governance", "relationships", "a~b")
    assert any(error.path == expected for error in rel_errors)


def test_dangling_fk_reference(tmp_path: Path) -> None:
    snap = build_snapshot()
    # Tamper model: point FK to missing table id but recompute identity
    ds = snap.model.data_sources[0]
    db = ds.databases[0]
    schema = db.schemas[0]
    table = next(t for t in schema.tables if t.name == "orders")
    fk = table.foreign_keys[0]
    broken_fk = ForeignKey(
        id=fk.id,
        name=fk.name,
        table_id=fk.table_id,
        column_ids=fk.column_ids,
        referenced_table_id="tbl:missing",
        referenced_column_ids=fk.referenced_column_ids,
    )
    from governance.domain import (
        Database,
        DataSource,
        GovernanceModel,
        Schema,
        Table,
    )

    new_table = Table(
        id=table.id,
        name=table.name,
        schema_id=table.schema_id,
        columns=table.columns,
        primary_key=table.primary_key,
        foreign_keys=(broken_fk,),
        description=table.description,
        ownership=table.ownership,
        technical_attributes=table.technical_attributes,
    )
    other = next(t for t in schema.tables if t.name != "orders")
    new_schema = Schema(
        id=schema.id,
        name=schema.name,
        database_id=schema.database_id,
        tables=(new_table, other),
        description=schema.description,
        ownership=schema.ownership,
    )
    new_db = Database(
        id=db.id,
        name=db.name,
        datasource_id=db.datasource_id,
        schemas=(new_schema,),
        description=db.description,
        ownership=db.ownership,
    )
    new_ds = DataSource(
        id=ds.id,
        name=ds.name,
        system_type=ds.system_type,
        databases=(new_db,),
        description=ds.description,
        ownership=ds.ownership,
        technical_attributes=ds.technical_attributes,
    )
    model = GovernanceModel(data_sources=(new_ds,), relationships=())
    broken = GovernanceSnapshot.from_model(model)
    with pytest.raises(ComparisonError) as exc_info:
        build_comparison_result(broken, broken)
    assert any(e.code == CODE_INVALID_SNAPSHOT_REFERENCES for e in exc_info.value.errors)


def test_unsupported_version_cli(tmp_path: Path) -> None:
    path = tmp_path / "snap.json"
    write_snapshot(build_snapshot(), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["snapshot_version"] = "99"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    other = tmp_path / "ok.json"
    write_snapshot(build_snapshot(), other)
    assert main(["compare", "--baseline", str(path), "--candidate", str(other)]) == 4
