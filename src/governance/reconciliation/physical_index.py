"""Physical reconciliation index from GovernanceModel (no local_id parsing)."""

from __future__ import annotations

from dataclasses import dataclass

from governance.domain.graph import (
    NODE_KIND_COLUMN,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    GraphNodeIdentity,
)
from governance.domain.models import GovernanceModel
from governance.domain.physical_projection import project_physical_local_id
from governance.reconciliation.errors import (
    CODE_OBJECT_IDENTITY_CONFLICT,
    DiagnosticError,
    ReconciliationError,
    objects_diagnostic_path,
)


@dataclass(frozen=True, slots=True)
class PhysicalReconciliationIndex:
    """Map Collibra/PG local_id → structural GraphNodeIdentity."""

    by_local_id: dict[str, GraphNodeIdentity]
    namespace: str

    def get(self, local_id: str) -> GraphNodeIdentity | None:
        return self.by_local_id.get(local_id)


def build_physical_reconciliation_index(
    model: GovernanceModel,
    *,
    namespace: str,
) -> PhysicalReconciliationIndex:
    """Build index from scanned model using existing object ids (never parse local_id)."""
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("namespace must be a non-empty string")
    ns = namespace.strip()
    mapping: dict[str, GraphNodeIdentity] = {}

    def _put(local_id: str, identity: GraphNodeIdentity) -> None:
        existing = mapping.get(local_id)
        if existing is not None and existing != identity:
            raise ReconciliationError(
                [
                    DiagnosticError(
                        code=CODE_OBJECT_IDENTITY_CONFLICT,
                        path=objects_diagnostic_path(local_id),
                        message=(
                            "physical reconciliation index has conflicting "
                            "GraphNodeIdentity for the same local_id"
                        ),
                    )
                ]
            )
        mapping[local_id] = identity

    for data_source in model.data_sources:
        for database in data_source.databases:
            db_identity = GraphNodeIdentity(
                namespace=ns,
                kind=NODE_KIND_DATA_SOURCE,
                logical_id=database.name,
                parent=None,
            )
            _put(database.id, db_identity)
            for schema in database.schemas:
                schema_identity = GraphNodeIdentity(
                    namespace=ns,
                    kind=NODE_KIND_DATASET,
                    logical_id=schema.name,
                    parent=db_identity,
                )
                _put(schema.id, schema_identity)
                for table in schema.tables:
                    table_identity = GraphNodeIdentity(
                        namespace=ns,
                        kind=NODE_KIND_TABLE,
                        logical_id=table.name,
                        parent=schema_identity,
                    )
                    _put(table.id, table_identity)
                    for column in table.columns:
                        column_identity = GraphNodeIdentity(
                            namespace=ns,
                            kind=NODE_KIND_COLUMN,
                            logical_id=column.name,
                            parent=table_identity,
                        )
                        _put(column.id, column_identity)

    return PhysicalReconciliationIndex(by_local_id=dict(mapping), namespace=ns)


def cross_check_known_objects(
    *,
    known_objects: tuple[GraphNodeIdentity, ...],
    physical_index: PhysicalReconciliationIndex,
) -> None:
    """Fail closed on structural identity conflicts; ignore non-projectable/off-PG."""
    by_candidate: dict[str, GraphNodeIdentity] = {}
    errors: list[DiagnosticError] = []

    for identity in known_objects:
        candidate = project_physical_local_id(identity)
        if candidate is None:
            continue
        prior = by_candidate.get(candidate)
        if prior is not None and prior != identity:
            errors.append(
                DiagnosticError(
                    code=CODE_OBJECT_IDENTITY_CONFLICT,
                    path=objects_diagnostic_path(candidate),
                    message=(
                        "external known_objects project to the same local_id "
                        "with conflicting GraphNodeIdentity"
                    ),
                )
            )
            continue
        by_candidate[candidate] = identity

        indexed = physical_index.get(candidate)
        if indexed is None:
            continue
        if indexed != identity:
            errors.append(
                DiagnosticError(
                    code=CODE_OBJECT_IDENTITY_CONFLICT,
                    path=objects_diagnostic_path(candidate),
                    message=(
                        "external known_object GraphNodeIdentity conflicts with "
                        "PhysicalReconciliationIndex identity for local_id"
                    ),
                )
            )

    if errors:
        # Dedupe identical diagnostics
        unique: dict[tuple[str, str, str], DiagnosticError] = {}
        for error in errors:
            unique[(error.path, error.code, error.message)] = error
        raise ReconciliationError(list(unique.values()))
