"""Serialize, persist, and load governance snapshots."""

from __future__ import annotations

import json
from pathlib import Path
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
from governance.identity import ContentIdentity, snapshot_identity
from governance.io.atomic import atomic_write_text
from governance.snapshots.errors import (
    SnapshotCompatibilityError,
    SnapshotIntegrityError,
    SnapshotIOError,
)
from governance.snapshots.models import SNAPSHOT_SCHEMA, SNAPSHOT_VERSION, GovernanceSnapshot


def snapshot_to_json(snapshot: GovernanceSnapshot) -> str:
    return (
        json.dumps(
            snapshot.to_dict(),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n"
    )


def write_snapshot(snapshot: GovernanceSnapshot, output_path: str | Path) -> Path:
    target = Path(output_path)
    try:
        return atomic_write_text(target, snapshot_to_json(snapshot))
    except OSError as exc:
        raise SnapshotIOError(f"Unable to write snapshot to {target}") from exc


def load_snapshot(path: str | Path) -> GovernanceSnapshot:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise SnapshotIOError(f"Unable to read snapshot from {target}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SnapshotCompatibilityError("invalid snapshot JSON") from exc

    if not isinstance(payload, dict):
        raise SnapshotCompatibilityError("snapshot root must be a mapping")

    schema = payload.get("snapshot_schema")
    version = payload.get("snapshot_version")
    if schema != SNAPSHOT_SCHEMA or version != SNAPSHOT_VERSION:
        raise SnapshotCompatibilityError("unsupported snapshot version")

    identity_raw = payload.get("content_identity")
    if not isinstance(identity_raw, dict):
        raise SnapshotCompatibilityError("snapshot content_identity is required")

    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    expected = snapshot_identity(without_identity)
    actual = ContentIdentity(
        algorithm=str(identity_raw.get("algorithm", "")),
        hashing_contract_version=str(identity_raw.get("hashing_contract_version", "")),
        digest=str(identity_raw.get("digest", "")),
    )
    if actual != expected:
        raise SnapshotIntegrityError("snapshot content_identity mismatch")

    try:
        model = _governance_from_dict(payload["governance"])
        source = payload["source"]
        scan = payload["scan"]
        return GovernanceSnapshot(
            model=model,
            source_name=str(source["name"]),
            database_name=str(source["database"]),
            system_type=str(source["system_type"]),
            scanner=str(scan["scanner"]),
            scanner_contract_version=str(scan["scanner_contract_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotCompatibilityError("invalid snapshot payload") from exc


def _governance_from_dict(raw: Any) -> GovernanceModel:
    if not isinstance(raw, dict):
        raise ValueError("governance must be a mapping")
    data_sources = tuple(_datasource_from_dict(item) for item in raw["data_sources"])
    relationships = tuple(_relationship_from_dict(item) for item in raw.get("relationships", []))
    return GovernanceModel(data_sources=data_sources, relationships=relationships)


def _ownership_from_dict(raw: Any) -> Ownership | None:
    if raw is None:
        return None
    return Ownership(
        owner_name=str(raw["owner_name"]),
        owner_type=str(raw.get("owner_type", "role")),
    )


def _datasource_from_dict(raw: dict[str, Any]) -> DataSource:
    return DataSource(
        id=str(raw["id"]),
        name=str(raw["name"]),
        system_type=str(raw["system_type"]),
        databases=tuple(_database_from_dict(item) for item in raw.get("databases", [])),
        description=raw.get("description"),
        ownership=_ownership_from_dict(raw.get("ownership")),
        technical_attributes=dict(raw.get("technical_attributes") or {}),
    )


def _database_from_dict(raw: dict[str, Any]) -> Database:
    return Database(
        id=str(raw["id"]),
        name=str(raw["name"]),
        datasource_id=str(raw["datasource_id"]),
        schemas=tuple(_schema_from_dict(item) for item in raw.get("schemas", [])),
        description=raw.get("description"),
        ownership=_ownership_from_dict(raw.get("ownership")),
    )


def _schema_from_dict(raw: dict[str, Any]) -> Schema:
    return Schema(
        id=str(raw["id"]),
        name=str(raw["name"]),
        database_id=str(raw["database_id"]),
        tables=tuple(_table_from_dict(item) for item in raw.get("tables", [])),
        description=raw.get("description"),
        ownership=_ownership_from_dict(raw.get("ownership")),
    )


def _table_from_dict(raw: dict[str, Any]) -> Table:
    pk_raw = raw.get("primary_key")
    primary_key = None
    if pk_raw is not None:
        primary_key = PrimaryKey(
            id=str(pk_raw["id"]),
            name=str(pk_raw["name"]),
            table_id=str(pk_raw["table_id"]),
            column_ids=tuple(str(item) for item in pk_raw["column_ids"]),
        )
    return Table(
        id=str(raw["id"]),
        name=str(raw["name"]),
        schema_id=str(raw["schema_id"]),
        columns=tuple(_column_from_dict(item) for item in raw.get("columns", [])),
        primary_key=primary_key,
        foreign_keys=tuple(_fk_from_dict(item) for item in raw.get("foreign_keys", [])),
        description=raw.get("description"),
        ownership=_ownership_from_dict(raw.get("ownership")),
        technical_attributes=dict(raw.get("technical_attributes") or {}),
    )


def _column_from_dict(raw: dict[str, Any]) -> Column:
    return Column(
        id=str(raw["id"]),
        name=str(raw["name"]),
        data_type=str(raw["data_type"]),
        ordinal_position=int(raw["ordinal_position"]),
        nullable=bool(raw["nullable"]),
        description=raw.get("description"),
        technical_attributes=dict(raw.get("technical_attributes") or {}),
    )


def _fk_from_dict(raw: dict[str, Any]) -> ForeignKey:
    return ForeignKey(
        id=str(raw["id"]),
        name=str(raw["name"]),
        table_id=str(raw["table_id"]),
        column_ids=tuple(str(item) for item in raw["column_ids"]),
        referenced_table_id=str(raw["referenced_table_id"]),
        referenced_column_ids=tuple(str(item) for item in raw["referenced_column_ids"]),
    )


def _relationship_from_dict(raw: dict[str, Any]) -> Relationship:
    return Relationship(
        id=str(raw["id"]),
        name=str(raw["name"]),
        from_table_id=str(raw["from_table_id"]),
        to_table_id=str(raw["to_table_id"]),
        foreign_key_id=raw.get("foreign_key_id"),
        description=raw.get("description"),
    )
