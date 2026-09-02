"""Strict snapshot loader tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest_comparison import build_sample_model
from conftest_history import write_sample_snapshot
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
from governance.identity import snapshot_identity
from governance.snapshots import (
    SNAPSHOT_SCHEMA,
    GovernanceSnapshot,
    SnapshotCompatibilityError,
    SnapshotIntegrityError,
    load_snapshot_artifact,
    snapshot_to_json,
    write_snapshot,
)


def _inject_duplicate_key(text: str, key: str, earlier_json_value: str) -> str:
    needle = f'"{key}":'
    index = text.index(needle)
    return text[:index] + f'  "{key}": {earlier_json_value},\n' + text[index:]


def _rewrite_with_valid_identity(path: Path, mutator: Callable[[dict[str, Any]], None]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutator(payload)
    without = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = snapshot_identity(without).to_dict()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _orders_table(payload: dict[str, Any]) -> dict[str, Any]:
    tables = payload["governance"]["data_sources"][0]["databases"][0]["schemas"][0]["tables"]
    return next(table for table in tables if table["name"] == "orders")


def _column_path(payload: dict[str, Any]) -> dict[str, Any]:
    return _orders_table(payload)["columns"][0]


def _table_path(payload: dict[str, Any]) -> dict[str, Any]:
    return _orders_table(payload)


def test_strict_load_round_trip(tmp_path: Path) -> None:
    snap = write_sample_snapshot(tmp_path / "a.json")
    loaded = load_snapshot_artifact(tmp_path / "a.json")
    assert loaded.content_identity() == snap.content_identity()
    payload = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))
    assert payload["snapshot_schema"] == SNAPSHOT_SCHEMA


def test_strict_integrity_mismatch(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    payload = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))
    payload["content_identity"]["digest"] = "0" * 64
    (tmp_path / "a.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SnapshotIntegrityError):
        load_snapshot_artifact(tmp_path / "a.json")


def test_strict_unsupported_version(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    payload = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))
    payload["snapshot_version"] = "99"
    (tmp_path / "a.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(tmp_path / "a.json")
    assert exc_info.value.code == "unsupported_snapshot_version"


def test_strict_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "parse_error"


def test_snapshot_loader_does_not_import_comparison() -> None:
    import governance.snapshots.load as load_mod
    import governance.snapshots.validate as validate_mod

    for module in (load_mod, validate_mod):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "governance.comparison" not in source
        assert "governance.history" not in source


def test_invalid_utf8_bytes_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    path.write_bytes(b'{"snapshot_schema": "\xff"}')
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "parse_error"
    assert exc_info.value.path == "/"


def test_duplicate_root_json_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_sample_snapshot(path)
    text = path.read_text(encoding="utf-8")
    path.write_text(_inject_duplicate_key(text, "snapshot_schema", '"other"'), encoding="utf-8")
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "parse_error"


def test_duplicate_nested_json_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_sample_snapshot(path)
    text = path.read_text(encoding="utf-8")
    path.write_text(_inject_duplicate_key(text, "algorithm", '"md5"'), encoding="utf-8")
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "parse_error"


@pytest.mark.parametrize(
    ("literal",),
    [
        ("NaN",),
        ("Infinity",),
        ("-Infinity",),
        ("1e999",),
    ],
)
def test_non_finite_json_literals_rejected(tmp_path: Path, literal: str) -> None:
    path = tmp_path / "a.json"
    path.write_text(f'{{"value": {literal}}}\n', encoding="utf-8")
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "parse_error"


def test_recursion_error_in_json_loads_maps_to_parse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_sample_snapshot(tmp_path / "a.json")

    def _boom(*args, **kwargs):
        raise RecursionError("too deep")

    monkeypatch.setattr("governance.snapshots.load.json.loads", _boom)
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(tmp_path / "a.json")
    assert exc_info.value.code == "parse_error"


def test_recursion_error_in_validate_json_value_maps_to_parse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    real_loads = json.loads

    def _loads_with_validate_boom(*args, **kwargs):
        payload = real_loads(*args, **kwargs)
        monkeypatch.setattr(
            "governance.snapshots.load.validate_json_value",
            lambda _value: (_ for _ in ()).throw(RecursionError("too deep")),
        )
        return payload

    monkeypatch.setattr("governance.snapshots.load.json.loads", _loads_with_validate_boom)
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(tmp_path / "a.json")
    assert exc_info.value.code == "parse_error"


def test_recursion_error_in_schema_iter_errors_maps_to_invalid_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_sample_snapshot(tmp_path / "a.json")

    class _BoomValidator:
        def iter_errors(self, _payload):
            raise RecursionError("too deep")

    monkeypatch.setattr(
        "governance.snapshots.load._get_validator",
        lambda: _BoomValidator(),
    )
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(tmp_path / "a.json")
    assert exc_info.value.code == "invalid_snapshot_payload"
    assert "too deeply nested" in str(exc_info.value)


def test_real_depth_nested_description_schema_smoke(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_sample_snapshot(path)
    nested: Any = "leaf"
    for _ in range(80):
        nested = {"nested": nested}

    def mutate(payload: dict[str, Any]) -> None:
        _orders_table(payload)["description"] = nested

    _rewrite_with_valid_identity(path, mutate)
    # Must not escape as an uncaught RecursionError; either accepts or maps cleanly.
    try:
        load_snapshot_artifact(path)
    except SnapshotCompatibilityError as exc:
        assert exc.code in {"invalid_snapshot_payload", "parse_error"}
        if exc.code == "invalid_snapshot_payload":
            assert "too deeply nested" in str(exc) or "invalid snapshot payload" in str(exc)


def test_bool_ordinal_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_sample_snapshot(path)

    def mutate(payload: dict[str, Any]) -> None:
        _column_path(payload)["ordinal_position"] = True

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "invalid_snapshot_payload"
    assert "ordinal_position" in exc_info.value.path


def test_ordinal_zero_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_sample_snapshot(path)

    def mutate(payload: dict[str, Any]) -> None:
        _column_path(payload)["ordinal_position"] = 0

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "invalid_snapshot_payload"


def test_nullable_nonbool_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_sample_snapshot(path)

    def mutate(payload: dict[str, Any]) -> None:
        _column_path(payload)["nullable"] = 0

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "invalid_snapshot_payload"
    assert "nullable" in exc_info.value.path


def test_malformed_ownership_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_sample_snapshot(path)

    def mutate(payload: dict[str, Any]) -> None:
        _table_path(payload)["ownership"] = {"owner_name": "", "owner_type": "role"}

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "invalid_snapshot_payload"


def test_malformed_primary_key_column_ref_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_sample_snapshot(path)

    def mutate(payload: dict[str, Any]) -> None:
        _table_path(payload)["primary_key"]["column_ids"] = ["missing-column-id"]

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "invalid_snapshot_payload"


def test_malformed_foreign_key_referenced_table_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_sample_snapshot(path)

    def mutate(payload: dict[str, Any]) -> None:
        fks = _table_path(payload)["foreign_keys"]
        assert fks
        fks[0]["referenced_table_id"] = "missing-table"

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "invalid_snapshot_payload"


def test_malformed_relationship_from_table_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_sample_snapshot(path)

    def mutate(payload: dict[str, Any]) -> None:
        payload["governance"]["relationships"][0]["from_table_id"] = "missing-table"

    _rewrite_with_valid_identity(path, mutate)
    with pytest.raises(SnapshotCompatibilityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "invalid_snapshot_payload"


def _model_with_layered_descriptions(
    *,
    ds_description: Any,
    db_description: Any,
    schema_description: Any,
    table_description: Any,
    column_description: Any,
    relationship_description: Any,
) -> GovernanceModel:
    source_name = "governance-demo"
    database_name = "governance_demo"
    system_type = "postgresql"
    schema_name = "sales"
    table_name = "orders"
    column_name = "id"
    ref_table_name = "customers"
    ref_column_name = "id"
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
    fk_id = make_foreign_key_id(table_id, f"{table_name}_{column_name}_fkey")
    column = Column(
        id=column_id,
        name=column_name,
        data_type="integer",
        ordinal_position=1,
        nullable=False,
        description=column_description,
    )
    foreign_key = ForeignKey(
        id=fk_id,
        name=f"{table_name}_{column_name}_fkey",
        table_id=table_id,
        column_ids=(column_id,),
        referenced_table_id=ref_table_id,
        referenced_column_ids=(ref_column_id,),
    )
    table = Table(
        id=table_id,
        name=table_name,
        schema_id=schema_id,
        columns=(column,),
        primary_key=PrimaryKey(
            id=pk_id,
            name=f"{table_name}_pkey",
            table_id=table_id,
            column_ids=(column_id,),
        ),
        foreign_keys=(foreign_key,),
        description=table_description,
        ownership=Ownership(owner_name="data-owner", owner_type="role"),
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
        description=schema_description,
    )
    database = Database(
        id=database_id,
        name=database_name,
        datasource_id=datasource_id,
        schemas=(schema,),
        description=db_description,
    )
    data_source = DataSource(
        id=datasource_id,
        name=source_name,
        system_type=system_type,
        databases=(database,),
        description=ds_description,
    )
    relationship = Relationship(
        id=make_relationship_id(fk_id),
        name=f"{table_name}_{column_name}_fkey",
        from_table_id=table_id,
        to_table_id=ref_table_id,
        foreign_key_id=fk_id,
        description=relationship_description,
    )
    return GovernanceModel(data_sources=(data_source,), relationships=(relationship,))


def test_description_structured_json_round_trips(tmp_path: Path) -> None:
    model = _model_with_layered_descriptions(
        ds_description={"note": "source"},
        db_description=["db", "meta"],
        schema_description=True,
        table_description={"table": "orders"},
        column_description=42,
        relationship_description={"edges": [{"from": "orders", "to": "customers"}]},
    )
    original = GovernanceSnapshot.from_model(model)
    path = tmp_path / "structured.json"
    write_snapshot(original, path)
    loaded = load_snapshot_artifact(path)
    assert loaded.content_identity() == original.content_identity()
    assert snapshot_to_json(loaded) == snapshot_to_json(original)
    ds = loaded.model.data_sources[0]
    db = ds.databases[0]
    schema = db.schemas[0]
    table = next(item for item in schema.tables if item.name == "orders")
    column = table.columns[0]
    relationship = loaded.model.relationships[0]
    assert ds.description == {"note": "source"}
    assert db.description == ["db", "meta"]
    assert schema.description is True
    assert table.description == {"table": "orders"}
    assert column.description == 42
    assert relationship.description == {"edges": [{"from": "orders", "to": "customers"}]}


def test_null_description_round_trip(tmp_path: Path) -> None:
    original = GovernanceSnapshot.from_model(build_sample_model(description=None))
    path = tmp_path / "null-desc.json"
    write_snapshot(original, path)
    loaded = load_snapshot_artifact(path)
    assert loaded.content_identity() == original.content_identity()
    assert snapshot_to_json(loaded) == snapshot_to_json(original)
    assert loaded.model.data_sources[0].description is None


def test_producer_valid_pk_fk_relationship_positive(tmp_path: Path) -> None:
    original = GovernanceSnapshot.from_model(build_sample_model(include_fk=True))
    path = tmp_path / "pkfk.json"
    write_snapshot(original, path)
    loaded = load_snapshot_artifact(path)
    assert loaded.content_identity() == original.content_identity()
    table = next(
        item
        for item in loaded.model.data_sources[0].databases[0].schemas[0].tables
        if item.name == "orders"
    )
    assert table.primary_key is not None
    assert table.foreign_keys
    assert loaded.model.relationships
    assert snapshot_to_json(loaded) == snapshot_to_json(original)


def test_content_identity_full_equality_required(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_sample_snapshot(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    without = {key: value for key, value in payload.items() if key != "content_identity"}
    expected = snapshot_identity(without).to_dict()
    payload["content_identity"] = {**expected, "digest": "0" * 64}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(SnapshotIntegrityError) as exc_info:
        load_snapshot_artifact(path)
    assert exc_info.value.code == "integrity_mismatch"
