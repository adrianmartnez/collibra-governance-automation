"""Projection and identity tests for snapshot comparison."""

from __future__ import annotations

import unicodedata

import pytest

from conftest_comparison import build_snapshot
from governance.comparison import (
    ComparisonError,
    ComparisonObjectIdentity,
    project_snapshot,
)
from governance.comparison.errors import (
    CODE_DUPLICATE_SNAPSHOT_OBJECT_ID,
    CODE_INVALID_SNAPSHOT_PAYLOAD,
    CODE_SCANNER_CONTRACT_MISMATCH,
    CODE_SNAPSHOT_ENVELOPE_MISMATCH,
)
from governance.comparison.projection import _diagnostic_path
from governance.domain.observations import PropertyPath
from governance.snapshots.models import GovernanceSnapshot


def test_comparison_identity_arity() -> None:
    assert ComparisonObjectIdentity(kind="data_source", path=()).path == ()
    assert ComparisonObjectIdentity(kind="database", path=()).path == ()
    assert ComparisonObjectIdentity(kind="schema", path=("sales",)).path == ("sales",)
    assert ComparisonObjectIdentity(kind="table", path=("sales", "orders")).path == (
        "sales",
        "orders",
    )
    with pytest.raises(ValueError):
        ComparisonObjectIdentity(kind="column", path=())
    with pytest.raises(ValueError):
        ComparisonObjectIdentity(kind="schema", path=("sales", "extra"))
    with pytest.raises(ValueError):
        ComparisonObjectIdentity(kind="table", path=("sales", ""))


def test_identity_case_and_whitespace_material() -> None:
    left = ComparisonObjectIdentity(kind="table", path=("Orders", "x"))
    right = ComparisonObjectIdentity(kind="table", path=("orders", "x"))
    assert left != right
    spaced = ComparisonObjectIdentity(kind="table", path=(" orders ", "x"))
    assert spaced != right


def test_identity_slash_segments_distinct() -> None:
    s1 = ComparisonObjectIdentity(kind="schema", path=("a/b",))
    s2 = ComparisonObjectIdentity(kind="schema", path=("a",))
    assert s1 != s2
    a = ComparisonObjectIdentity(kind="column", path=("a/b", "t", "c"))
    b = ComparisonObjectIdentity(kind="column", path=("a", "b/t", "c"))
    assert a.canonical_bytes() != b.canonical_bytes()


def test_project_all_kinds() -> None:
    snapshot = build_snapshot()
    projected = project_snapshot(snapshot, side="baseline")
    kinds = {identity.kind for identity in projected.objects}
    assert kinds == {
        "data_source",
        "database",
        "schema",
        "table",
        "column",
        "primary_key",
        "foreign_key",
        "relationship",
    }
    fk = next(obj for ident, obj in projected.objects.items() if ident.kind == "foreign_key")
    ref_table = fk.properties["/referenced_table_id"].value
    assert ref_table["kind"] == "table"
    assert isinstance(ref_table["path"], list)


def test_empty_technical_attribute_key_pointer() -> None:
    snapshot = build_snapshot(technical_attributes={"": None})
    projected = project_snapshot(snapshot, side="baseline")
    ds = next(obj for ident, obj in projected.objects.items() if ident.kind == "data_source")
    pointer = PropertyPath(("technical_attributes", "")).to_pointer()
    assert pointer == "/technical_attributes/"
    assert pointer in ds.properties
    assert ds.properties[pointer].has_value is True
    assert ds.properties[pointer].value is None
    assert PropertyPath.parse(pointer).segments == ("technical_attributes", "")


def test_technical_attribute_slash_and_tilde_pointers() -> None:
    snapshot = build_snapshot(technical_attributes={"a/b": 1, "a~b": 2})
    projected = project_snapshot(snapshot, side="baseline")
    ds = next(obj for ident, obj in projected.objects.items() if ident.kind == "data_source")
    slash_pointer = PropertyPath(("technical_attributes", "a/b")).to_pointer()
    tilde_pointer = PropertyPath(("technical_attributes", "a~b")).to_pointer()
    assert slash_pointer == "/technical_attributes/a~1b"
    assert tilde_pointer == "/technical_attributes/a~0b"
    assert ds.properties[slash_pointer].value == 1
    assert ds.properties[tilde_pointer].value == 2


def test_unicode_composed_decomposed_remain_distinct() -> None:
    composed = "café"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    left = ComparisonObjectIdentity(kind="schema", path=(composed,))
    right = ComparisonObjectIdentity(kind="schema", path=(decomposed,))
    assert left != right


def test_diagnostic_path_rfc6901_roundtrip() -> None:
    path = _diagnostic_path("baseline", "governance", "foreign_keys", "a/b", "referenced_table_id")
    assert path == "/baseline/governance/foreign_keys/a~1b/referenced_table_id"
    assert PropertyPath.parse(path).segments == (
        "baseline",
        "governance",
        "foreign_keys",
        "a/b",
        "referenced_table_id",
    )
    tilde_path = _diagnostic_path("candidate", "governance", "relationships", "a~b")
    assert tilde_path == "/candidate/governance/relationships/a~0b"
    assert PropertyPath.parse(tilde_path).segments == (
        "candidate",
        "governance",
        "relationships",
        "a~b",
    )


def test_duplicate_object_id_with_slash_diagnostic_path() -> None:
    snapshot = build_snapshot()
    ds = snapshot.model.data_sources[0]
    db = ds.databases[0]
    schema = db.schemas[0]
    table = next(t for t in schema.tables if t.name == "orders")
    duplicate_id = "obj/with/slash"
    from governance.domain import Column, Table

    dup_column = Column(
        id=duplicate_id,
        name="dup",
        data_type="text",
        ordinal_position=99,
        nullable=True,
    )
    dup_table = Table(
        id=duplicate_id,
        name="dup_table",
        schema_id=schema.id,
        columns=(dup_column,),
    )
    from governance.domain import Database, DataSource, GovernanceModel, Schema

    new_schema = Schema(
        id=schema.id,
        name=schema.name,
        database_id=schema.database_id,
        tables=(*schema.tables, dup_table),
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
    broken = GovernanceSnapshot.from_model(
        GovernanceModel(data_sources=(new_ds,), relationships=snapshot.model.relationships)
    )
    with pytest.raises(ComparisonError) as exc_info:
        project_snapshot(broken, side="baseline")
    dup_errors = [e for e in exc_info.value.errors if e.code == CODE_DUPLICATE_SNAPSHOT_OBJECT_ID]
    assert dup_errors
    expected = _diagnostic_path("baseline", "objects", duplicate_id)
    assert dup_errors[0].path == expected
    assert PropertyPath.parse(expected).segments[-1] == duplicate_id
    assert table.id != duplicate_id


def test_envelope_mismatch() -> None:
    snapshot = build_snapshot()
    broken = GovernanceSnapshot(
        model=snapshot.model,
        source_name="other-source",
        database_name=snapshot.database_name,
        system_type=snapshot.system_type,
        scanner=snapshot.scanner,
        scanner_contract_version=snapshot.scanner_contract_version,
    )
    with pytest.raises(ComparisonError) as exc_info:
        project_snapshot(broken, side="baseline")
    assert any(e.code == CODE_SNAPSHOT_ENVELOPE_MISMATCH for e in exc_info.value.errors)


def test_scanner_contract_mismatch() -> None:
    snapshot = build_snapshot()
    broken = GovernanceSnapshot(
        model=snapshot.model,
        source_name=snapshot.source_name,
        database_name=snapshot.database_name,
        system_type=snapshot.system_type,
        scanner=snapshot.scanner,
        scanner_contract_version="999",
    )
    with pytest.raises(ComparisonError) as exc_info:
        project_snapshot(broken, side="candidate")
    assert any(e.code == CODE_SCANNER_CONTRACT_MISMATCH for e in exc_info.value.errors)


def test_root_shape_multiple_databases() -> None:
    snapshot = build_snapshot()
    ds = snapshot.model.data_sources[0]
    from governance.domain import Database, DataSource, GovernanceModel, make_database_id

    extra = Database(
        id=make_database_id(ds.name, "other_db"),
        name="other_db",
        datasource_id=ds.id,
        schemas=(),
    )
    model = GovernanceModel(
        data_sources=(
            DataSource(
                id=ds.id,
                name=ds.name,
                system_type=ds.system_type,
                databases=(ds.databases[0], extra),
            ),
        )
    )
    broken = GovernanceSnapshot(
        model=model,
        source_name=ds.name,
        database_name=ds.databases[0].name,
        system_type=ds.system_type,
        scanner=snapshot.scanner,
        scanner_contract_version=snapshot.scanner_contract_version,
    )
    with pytest.raises(ComparisonError) as exc_info:
        project_snapshot(broken, side="baseline")
    assert any(e.code == CODE_INVALID_SNAPSHOT_PAYLOAD for e in exc_info.value.errors)
