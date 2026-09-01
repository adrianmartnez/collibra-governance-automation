"""Project governance snapshots into comparable objects with structured identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from governance.comparison.errors import (
    CODE_DUPLICATE_COMPARISON_IDENTITY,
    CODE_DUPLICATE_SNAPSHOT_OBJECT_ID,
    CODE_INVALID_SNAPSHOT_PAYLOAD,
    CODE_INVALID_SNAPSHOT_REFERENCES,
    CODE_SCANNER_CONTRACT_MISMATCH,
    CODE_SNAPSHOT_ENVELOPE_MISMATCH,
    ComparisonError,
    DiagnosticError,
)
from governance.domain.models import (
    Column,
    ForeignKey,
    GovernanceModel,
    Ownership,
    Schema,
    Table,
)
from governance.domain.observations import PropertyPath
from governance.exporters.inventory import SCANNER_CONTRACT_VERSION
from governance.identity.canonicalize import canonical_json_bytes
from governance.identity.json_values import normalize_json_value
from governance.snapshots.models import GovernanceSnapshot

GovernedObjectKind = Literal[
    "data_source",
    "database",
    "schema",
    "table",
    "column",
    "primary_key",
    "foreign_key",
    "relationship",
]

_PATH_ARITY: dict[GovernedObjectKind, int] = {
    "data_source": 0,
    "database": 0,
    "schema": 1,
    "table": 2,
    "column": 3,
    "primary_key": 3,
    "foreign_key": 3,
    "relationship": 3,
}


@dataclass(frozen=True, slots=True)
class ComparisonObjectIdentity:
    """Structured comparison identity — never delimiter-joined for matching."""

    kind: GovernedObjectKind
    path: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in _PATH_ARITY:
            raise ValueError(f"unsupported comparison kind: {self.kind!r}")
        expected = _PATH_ARITY[self.kind]
        if len(self.path) != expected:
            raise ValueError(
                f"{self.kind} identity requires path arity {expected}, got {len(self.path)}"
            )
        if expected > 0:
            for segment in self.path:
                if not isinstance(segment, str):
                    raise TypeError("identity path segments must be strings")
                if segment == "":
                    raise ValueError("identity path segments must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": list(self.path)}

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ComparisonObjectIdentity):
            return NotImplemented
        return self.canonical_bytes() < other.canonical_bytes()


@dataclass(frozen=True, slots=True)
class ComparablePropertyValue:
    """Presence-aware property value for comparison."""

    has_value: bool
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        if self.has_value:
            return {"has_value": True, "value": self.value}
        return {"has_value": False}


@dataclass(frozen=True, slots=True)
class ComparableObject:
    object_identity: ComparisonObjectIdentity
    parent_identity: ComparisonObjectIdentity | None
    properties: dict[str, ComparablePropertyValue]


@dataclass(frozen=True, slots=True)
class ProjectedSnapshot:
    """Fully validated projection of one snapshot side."""

    objects: dict[ComparisonObjectIdentity, ComparableObject]
    raw_to_identity: dict[str, ComparisonObjectIdentity]
    snapshot: GovernanceSnapshot


def _diagnostic_path(side: str, *segments: str) -> str:
    return PropertyPath((side, *segments)).to_pointer()


def _present(value: Any) -> ComparablePropertyValue:
    return ComparablePropertyValue(has_value=True, value=normalize_json_value(value))


def _missing() -> ComparablePropertyValue:
    return ComparablePropertyValue(has_value=False)


def _ownership_value(ownership: Ownership | None) -> Any:
    if ownership is None:
        return None
    return ownership.to_dict()


def _identity_ref(identity: ComparisonObjectIdentity) -> dict[str, Any]:
    return identity.to_dict()


def validate_snapshot_shape_and_envelope(
    snapshot: GovernanceSnapshot,
    *,
    side: str,
) -> None:
    """Validate root shape and envelope/model coherence before projection."""
    errors: list[DiagnosticError] = []
    model = snapshot.model
    if len(model.data_sources) != 1:
        errors.append(
            DiagnosticError(
                code=CODE_INVALID_SNAPSHOT_PAYLOAD,
                path=_diagnostic_path(side, "governance"),
                message="snapshot must contain exactly one data_source",
            )
        )
        raise ComparisonError(errors)

    data_source = model.data_sources[0]
    if len(data_source.databases) != 1:
        errors.append(
            DiagnosticError(
                code=CODE_INVALID_SNAPSHOT_PAYLOAD,
                path=_diagnostic_path(side, "governance"),
                message="snapshot must contain exactly one database",
            )
        )
        raise ComparisonError(errors)

    database = data_source.databases[0]

    if snapshot.source_name != data_source.name:
        errors.append(
            DiagnosticError(
                code=CODE_SNAPSHOT_ENVELOPE_MISMATCH,
                path=_diagnostic_path(side, "source", "name"),
                message="envelope source name does not match model data_source.name",
            )
        )
    if snapshot.database_name != database.name:
        errors.append(
            DiagnosticError(
                code=CODE_SNAPSHOT_ENVELOPE_MISMATCH,
                path=_diagnostic_path(side, "source", "database"),
                message="envelope database name does not match model database.name",
            )
        )
    if snapshot.system_type != data_source.system_type:
        errors.append(
            DiagnosticError(
                code=CODE_SNAPSHOT_ENVELOPE_MISMATCH,
                path=_diagnostic_path(side, "source", "system_type"),
                message="envelope system_type does not match model data_source.system_type",
            )
        )

    if not isinstance(snapshot.scanner, str) or snapshot.scanner == "":
        errors.append(
            DiagnosticError(
                code=CODE_INVALID_SNAPSHOT_PAYLOAD,
                path=_diagnostic_path(side, "scan", "scanner"),
                message="scanner must be a non-empty string",
            )
        )
    if (
        not isinstance(snapshot.system_type, str)
        or snapshot.system_type == ""
        or not isinstance(snapshot.scanner_contract_version, str)
        or snapshot.scanner_contract_version == ""
    ):
        errors.append(
            DiagnosticError(
                code=CODE_INVALID_SNAPSHOT_PAYLOAD,
                path=_diagnostic_path(side, "scan"),
                message="system_type and scanner_contract_version must be non-empty strings",
            )
        )

    if snapshot.scanner_contract_version != SCANNER_CONTRACT_VERSION:
        errors.append(
            DiagnosticError(
                code=CODE_SCANNER_CONTRACT_MISMATCH,
                path=_diagnostic_path(side, "scan", "scanner_contract_version"),
                message=(
                    f"scanner_contract_version must be {SCANNER_CONTRACT_VERSION!r}; "
                    f"got {snapshot.scanner_contract_version!r}"
                ),
            )
        )

    if errors:
        raise ComparisonError(errors)


def project_snapshot(snapshot: GovernanceSnapshot, *, side: str) -> ProjectedSnapshot:
    """Project one snapshot into comparable objects after shape/envelope validation."""
    validate_snapshot_shape_and_envelope(snapshot, side=side)
    model = snapshot.model
    data_source = model.data_sources[0]
    database = data_source.databases[0]

    raw_to_identity: dict[str, ComparisonObjectIdentity] = {}
    identity_to_raw: dict[ComparisonObjectIdentity, str] = {}
    objects: dict[ComparisonObjectIdentity, ComparableObject] = {}
    errors: list[DiagnosticError] = []

    def register(
        raw_id: str,
        identity: ComparisonObjectIdentity,
        parent: ComparisonObjectIdentity | None,
        properties: dict[str, ComparablePropertyValue],
    ) -> None:
        if raw_id in raw_to_identity:
            errors.append(
                DiagnosticError(
                    code=CODE_DUPLICATE_SNAPSHOT_OBJECT_ID,
                    path=_diagnostic_path(side, "objects", raw_id),
                    message=f"duplicate snapshot object id: {raw_id}",
                )
            )
            return
        if identity in identity_to_raw:
            errors.append(
                DiagnosticError(
                    code=CODE_DUPLICATE_COMPARISON_IDENTITY,
                    path=_diagnostic_path(side, "identity", identity.kind),
                    message=f"duplicate comparison identity for kind={identity.kind}",
                )
            )
            return
        raw_to_identity[raw_id] = identity
        identity_to_raw[identity] = raw_id
        objects[identity] = ComparableObject(
            object_identity=identity,
            parent_identity=parent,
            properties=properties,
        )

    ds_identity = ComparisonObjectIdentity(kind="data_source", path=())
    ds_props = _tech_props(
        {
            "/name": _present(data_source.name),
            "/system_type": _present(data_source.system_type),
            "/description": _present(data_source.description),
            "/ownership": _present(_ownership_value(data_source.ownership)),
        },
        data_source.technical_attributes,
    )
    register(data_source.id, ds_identity, None, ds_props)

    db_identity = ComparisonObjectIdentity(kind="database", path=())
    if data_source.databases[0].datasource_id != data_source.id:
        errors.append(
            DiagnosticError(
                code=CODE_INVALID_SNAPSHOT_REFERENCES,
                path=_diagnostic_path(side, "governance", "database", "datasource_id"),
                message="database.datasource_id does not resolve to data_source",
            )
        )
    db_props = {
        "/name": _present(database.name),
        "/description": _present(database.description),
        "/ownership": _present(_ownership_value(database.ownership)),
    }
    register(database.id, db_identity, ds_identity, db_props)

    # First pass: register all objects with provisional properties (refs as raw → resolve later)
    schema_by_id: dict[str, Schema] = {}
    table_by_id: dict[str, Table] = {}
    column_by_id: dict[str, Column] = {}
    fk_by_id: dict[str, ForeignKey] = {}

    for schema in database.schemas:
        schema_by_id[schema.id] = schema
        if schema.database_id != database.id:
            errors.append(
                DiagnosticError(
                    code=CODE_INVALID_SNAPSHOT_REFERENCES,
                    path=_diagnostic_path(
                        side, "governance", "schemas", schema.name, "database_id"
                    ),
                    message="schema.database_id does not resolve to database",
                )
            )
        sch_identity = ComparisonObjectIdentity(kind="schema", path=(schema.name,))
        register(
            schema.id,
            sch_identity,
            db_identity,
            {
                "/name": _present(schema.name),
                "/description": _present(schema.description),
                "/ownership": _present(_ownership_value(schema.ownership)),
            },
        )

        for table in schema.tables:
            table_by_id[table.id] = table
            if table.schema_id != schema.id:
                errors.append(
                    DiagnosticError(
                        code=CODE_INVALID_SNAPSHOT_REFERENCES,
                        path=_diagnostic_path(
                            side,
                            "governance",
                            "tables",
                            schema.name,
                            table.name,
                            "schema_id",
                        ),
                        message="table.schema_id does not resolve to containing schema",
                    )
                )
            tbl_identity = ComparisonObjectIdentity(kind="table", path=(schema.name, table.name))
            register(
                table.id,
                tbl_identity,
                sch_identity,
                _tech_props(
                    {
                        "/name": _present(table.name),
                        "/description": _present(table.description),
                        "/ownership": _present(_ownership_value(table.ownership)),
                    },
                    table.technical_attributes,
                ),
            )

            for column in table.columns:
                column_by_id[column.id] = column
                col_identity = ComparisonObjectIdentity(
                    kind="column",
                    path=(schema.name, table.name, column.name),
                )
                register(
                    column.id,
                    col_identity,
                    tbl_identity,
                    _tech_props(
                        {
                            "/name": _present(column.name),
                            "/data_type": _present(column.data_type),
                            "/ordinal_position": _present(column.ordinal_position),
                            "/nullable": _present(column.nullable),
                            "/description": _present(column.description),
                        },
                        column.technical_attributes,
                    ),
                )

            if table.primary_key is not None:
                pk = table.primary_key
                if pk.table_id != table.id:
                    errors.append(
                        DiagnosticError(
                            code=CODE_INVALID_SNAPSHOT_REFERENCES,
                            path=_diagnostic_path(
                                side,
                                "governance",
                                "primary_keys",
                                schema.name,
                                table.name,
                                pk.name,
                                "table_id",
                            ),
                            message="primary_key.table_id does not resolve to containing table",
                        )
                    )
                pk_identity = ComparisonObjectIdentity(
                    kind="primary_key",
                    path=(schema.name, table.name, pk.name),
                )
                # column_ids resolved after full registration
                register(
                    pk.id,
                    pk_identity,
                    tbl_identity,
                    {
                        "/name": _present(pk.name),
                        "/column_ids": _present([]),  # placeholder; patched below
                    },
                )

            for fk in table.foreign_keys:
                fk_by_id[fk.id] = fk
                if fk.table_id != table.id:
                    errors.append(
                        DiagnosticError(
                            code=CODE_INVALID_SNAPSHOT_REFERENCES,
                            path=_diagnostic_path(
                                side,
                                "governance",
                                "foreign_keys",
                                schema.name,
                                table.name,
                                fk.name,
                                "table_id",
                            ),
                            message="foreign_key.table_id does not resolve to containing table",
                        )
                    )
                fk_identity = ComparisonObjectIdentity(
                    kind="foreign_key",
                    path=(schema.name, table.name, fk.name),
                )
                register(
                    fk.id,
                    fk_identity,
                    tbl_identity,
                    {
                        "/name": _present(fk.name),
                        "/column_ids": _present([]),
                        "/referenced_table_id": _present({"kind": "table", "path": []}),
                        "/referenced_column_ids": _present([]),
                    },
                )

    for relationship in model.relationships:
        from_table = table_by_id.get(relationship.from_table_id)
        if from_table is None:
            errors.append(
                DiagnosticError(
                    code=CODE_INVALID_SNAPSHOT_REFERENCES,
                    path=_diagnostic_path(side, "governance", "relationships", relationship.name),
                    message="relationship.from_table_id does not resolve",
                )
            )
            continue
        from_schema = schema_by_id.get(from_table.schema_id)
        if from_schema is None:
            errors.append(
                DiagnosticError(
                    code=CODE_INVALID_SNAPSHOT_REFERENCES,
                    path=_diagnostic_path(side, "governance", "relationships", relationship.name),
                    message="relationship source schema unresolved",
                )
            )
            continue
        rel_identity = ComparisonObjectIdentity(
            kind="relationship",
            path=(from_schema.name, from_table.name, relationship.name),
        )
        parent_tbl = ComparisonObjectIdentity(
            kind="table", path=(from_schema.name, from_table.name)
        )
        register(
            relationship.id,
            rel_identity,
            parent_tbl,
            {
                "/name": _present(relationship.name),
                "/to_table_id": _present({"kind": "table", "path": []}),
                "/foreign_key_id": _present(None),
                "/description": _present(relationship.description),
            },
        )

    if errors:
        raise ComparisonError(errors)

    # Second pass: normalize material refs and validate closure
    _patch_pk_fk_refs(
        objects=objects,
        raw_to_identity=raw_to_identity,
        table_by_id=table_by_id,
        column_by_id=column_by_id,
        side=side,
        errors=errors,
    )
    _patch_relationship_refs(
        model=model,
        objects=objects,
        raw_to_identity=raw_to_identity,
        table_by_id=table_by_id,
        schema_by_id=schema_by_id,
        fk_by_id=fk_by_id,
        side=side,
        errors=errors,
    )

    if errors:
        raise ComparisonError(errors)

    return ProjectedSnapshot(
        objects=objects,
        raw_to_identity=raw_to_identity,
        snapshot=snapshot,
    )


def _tech_props(
    base: dict[str, ComparablePropertyValue],
    technical_attributes: dict[str, Any],
) -> dict[str, ComparablePropertyValue]:
    props = dict(base)
    for key, value in technical_attributes.items():
        pointer = PropertyPath(("technical_attributes", key)).to_pointer()
        props[pointer] = _present(value)
    return props


def _resolve_column_refs(
    column_ids: tuple[str, ...],
    *,
    expected_table_id: str,
    raw_to_identity: dict[str, ComparisonObjectIdentity],
    column_by_id: dict[str, Column],
    table_by_id: dict[str, Table],
    side: str,
    path_segments: tuple[str, ...],
    errors: list[DiagnosticError],
) -> list[dict[str, Any]] | None:
    resolved: list[dict[str, Any]] = []
    table = table_by_id.get(expected_table_id)
    if table is None:
        errors.append(
            DiagnosticError(
                code=CODE_INVALID_SNAPSHOT_REFERENCES,
                path=_diagnostic_path(side, *path_segments),
                message="parent table unresolved for column refs",
            )
        )
        return None
    table_column_ids = {column.id for column in table.columns}
    for column_id in column_ids:
        column = column_by_id.get(column_id)
        identity = raw_to_identity.get(column_id)
        if column is None or identity is None or identity.kind != "column":
            errors.append(
                DiagnosticError(
                    code=CODE_INVALID_SNAPSHOT_REFERENCES,
                    path=_diagnostic_path(side, *path_segments),
                    message=f"column ref does not resolve: {column_id}",
                )
            )
            return None
        if column_id not in table_column_ids:
            errors.append(
                DiagnosticError(
                    code=CODE_INVALID_SNAPSHOT_REFERENCES,
                    path=_diagnostic_path(side, *path_segments),
                    message=f"column {column_id} does not belong to parent table",
                )
            )
            return None
        resolved.append(_identity_ref(identity))
    return resolved


def _patch_pk_fk_refs(
    *,
    objects: dict[ComparisonObjectIdentity, ComparableObject],
    raw_to_identity: dict[str, ComparisonObjectIdentity],
    table_by_id: dict[str, Table],
    column_by_id: dict[str, Column],
    side: str,
    errors: list[DiagnosticError],
) -> None:
    for table in table_by_id.values():
        if table.primary_key is not None:
            pk = table.primary_key
            pk_identity = raw_to_identity.get(pk.id)
            if pk_identity is None:
                continue
            refs = _resolve_column_refs(
                pk.column_ids,
                expected_table_id=table.id,
                raw_to_identity=raw_to_identity,
                column_by_id=column_by_id,
                table_by_id=table_by_id,
                side=side,
                path_segments=("governance", "primary_keys", pk.name, "column_ids"),
                errors=errors,
            )
            if refs is not None:
                obj = objects[pk_identity]
                objects[pk_identity] = ComparableObject(
                    object_identity=obj.object_identity,
                    parent_identity=obj.parent_identity,
                    properties={
                        **obj.properties,
                        "/column_ids": _present(refs),
                    },
                )

        for fk in table.foreign_keys:
            fk_identity = raw_to_identity.get(fk.id)
            if fk_identity is None:
                continue
            source_refs = _resolve_column_refs(
                fk.column_ids,
                expected_table_id=table.id,
                raw_to_identity=raw_to_identity,
                column_by_id=column_by_id,
                table_by_id=table_by_id,
                side=side,
                path_segments=("governance", "foreign_keys", fk.name, "column_ids"),
                errors=errors,
            )
            ref_table_identity = raw_to_identity.get(fk.referenced_table_id)
            if ref_table_identity is None or ref_table_identity.kind != "table":
                errors.append(
                    DiagnosticError(
                        code=CODE_INVALID_SNAPSHOT_REFERENCES,
                        path=_diagnostic_path(
                            side,
                            "governance",
                            "foreign_keys",
                            fk.name,
                            "referenced_table_id",
                        ),
                        message="referenced_table_id does not resolve to a table",
                    )
                )
                continue
            if len(fk.column_ids) != len(fk.referenced_column_ids):
                errors.append(
                    DiagnosticError(
                        code=CODE_INVALID_SNAPSHOT_REFERENCES,
                        path=_diagnostic_path(side, "governance", "foreign_keys", fk.name),
                        message="column_ids and referenced_column_ids length mismatch",
                    )
                )
                continue
            ref_cols = _resolve_column_refs(
                fk.referenced_column_ids,
                expected_table_id=fk.referenced_table_id,
                raw_to_identity=raw_to_identity,
                column_by_id=column_by_id,
                table_by_id=table_by_id,
                side=side,
                path_segments=(
                    "governance",
                    "foreign_keys",
                    fk.name,
                    "referenced_column_ids",
                ),
                errors=errors,
            )
            if source_refs is None or ref_cols is None:
                continue
            obj = objects[fk_identity]
            objects[fk_identity] = ComparableObject(
                object_identity=obj.object_identity,
                parent_identity=obj.parent_identity,
                properties={
                    **obj.properties,
                    "/column_ids": _present(source_refs),
                    "/referenced_table_id": _present(_identity_ref(ref_table_identity)),
                    "/referenced_column_ids": _present(ref_cols),
                },
            )


def _patch_relationship_refs(
    *,
    model: GovernanceModel,
    objects: dict[ComparisonObjectIdentity, ComparableObject],
    raw_to_identity: dict[str, ComparisonObjectIdentity],
    table_by_id: dict[str, Table],
    schema_by_id: dict[str, Schema],
    fk_by_id: dict[str, ForeignKey],
    side: str,
    errors: list[DiagnosticError],
) -> None:
    for relationship in model.relationships:
        rel_identity = raw_to_identity.get(relationship.id)
        if rel_identity is None:
            continue
        to_identity = raw_to_identity.get(relationship.to_table_id)
        if to_identity is None or to_identity.kind != "table":
            errors.append(
                DiagnosticError(
                    code=CODE_INVALID_SNAPSHOT_REFERENCES,
                    path=_diagnostic_path(
                        side,
                        "governance",
                        "relationships",
                        relationship.name,
                        "to_table_id",
                    ),
                    message="relationship.to_table_id does not resolve to a table",
                )
            )
            continue

        fk_value: Any = None
        if relationship.foreign_key_id is not None:
            fk = fk_by_id.get(relationship.foreign_key_id)
            fk_identity = raw_to_identity.get(relationship.foreign_key_id)
            if fk is None or fk_identity is None or fk_identity.kind != "foreign_key":
                errors.append(
                    DiagnosticError(
                        code=CODE_INVALID_SNAPSHOT_REFERENCES,
                        path=_diagnostic_path(
                            side,
                            "governance",
                            "relationships",
                            relationship.name,
                            "foreign_key_id",
                        ),
                        message="relationship.foreign_key_id does not resolve to a foreign_key",
                    )
                )
                continue
            if fk.table_id != relationship.from_table_id:
                errors.append(
                    DiagnosticError(
                        code=CODE_INVALID_SNAPSHOT_REFERENCES,
                        path=_diagnostic_path(
                            side,
                            "governance",
                            "relationships",
                            relationship.name,
                            "foreign_key_id",
                        ),
                        message=(
                            "foreign_key source table does not match relationship.from_table_id"
                        ),
                    )
                )
                continue
            if fk.referenced_table_id != relationship.to_table_id:
                errors.append(
                    DiagnosticError(
                        code=CODE_INVALID_SNAPSHOT_REFERENCES,
                        path=_diagnostic_path(
                            side,
                            "governance",
                            "relationships",
                            relationship.name,
                            "foreign_key_id",
                        ),
                        message=(
                            "foreign_key referenced table does not match relationship.to_table_id"
                        ),
                    )
                )
                continue
            fk_value = _identity_ref(fk_identity)

        # from_table_id validated structurally via identity construction
        from_table = table_by_id.get(relationship.from_table_id)
        if from_table is None or from_table.schema_id not in schema_by_id:
            errors.append(
                DiagnosticError(
                    code=CODE_INVALID_SNAPSHOT_REFERENCES,
                    path=_diagnostic_path(
                        side,
                        "governance",
                        "relationships",
                        relationship.name,
                        "from_table_id",
                    ),
                    message="relationship.from_table_id does not resolve",
                )
            )
            continue

        obj = objects[rel_identity]
        objects[rel_identity] = ComparableObject(
            object_identity=obj.object_identity,
            parent_identity=obj.parent_identity,
            properties={
                **obj.properties,
                "/to_table_id": _present(_identity_ref(to_identity)),
                "/foreign_key_id": _present(fk_value),
            },
        )
