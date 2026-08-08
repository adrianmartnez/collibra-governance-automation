"""Deterministic policy evaluation against GovernanceModel."""

from __future__ import annotations

from collections.abc import Iterator

from governance.domain.models import (
    GovernanceModel,
)
from governance.policy.models import (
    NormalizedPolicy,
    NormalizedPolicySet,
    ObjectKind,
    PolicySelector,
    PolicyViolation,
)


def evaluate_policies(
    model: GovernanceModel,
    policy_set: NormalizedPolicySet,
) -> tuple[PolicyViolation, ...]:
    violations: list[PolicyViolation] = []
    for policy in policy_set.policies:
        violations.extend(_evaluate_one(model, policy))
    return tuple(
        sorted(
            violations,
            key=lambda item: (
                0 if item.severity == "error" else 1,
                item.policy_id,
                item.object_kind,
                item.object_id,
                item.rule_type,
                item.reason,
            ),
        )
    )


def _evaluate_one(
    model: GovernanceModel,
    policy: NormalizedPolicy,
) -> list[PolicyViolation]:
    selected = list(_select_objects(model, policy.select))
    violations: list[PolicyViolation] = []
    for kind, object_id, object_name, obj in selected:
        if policy.rule_type == "require_owner":
            ownership = getattr(obj, "ownership", None)
            if ownership is None or not str(ownership.owner_name).strip():
                violations.append(
                    PolicyViolation(
                        policy_id=policy.id,
                        rule_type=policy.rule_type,
                        severity=policy.severity,
                        object_kind=kind,
                        object_id=object_id,
                        object_name=object_name,
                        reason="ownership is required but missing",
                    )
                )
        elif policy.rule_type == "require_description":
            description = getattr(obj, "description", None)
            if description is None or not str(description).strip():
                violations.append(
                    PolicyViolation(
                        policy_id=policy.id,
                        rule_type=policy.rule_type,
                        severity=policy.severity,
                        object_kind=kind,
                        object_id=object_id,
                        object_name=object_name,
                        reason="description is required but missing",
                    )
                )
        elif policy.rule_type == "require_relationship":
            related = any(
                rel.from_table_id == object_id or rel.to_table_id == object_id
                for rel in model.relationships
            )
            if not related:
                violations.append(
                    PolicyViolation(
                        policy_id=policy.id,
                        rule_type=policy.rule_type,
                        severity=policy.severity,
                        object_kind=kind,
                        object_id=object_id,
                        object_name=object_name,
                        reason="table must participate in at least one relationship",
                    )
                )
    return violations


def _select_objects(
    model: GovernanceModel,
    selector: PolicySelector,
) -> Iterator[tuple[ObjectKind, str, str | None, object]]:
    for kind, object_id, object_name, obj in _iter_objects(model):
        if kind != selector.kind:
            continue
        if selector.object_id is not None and object_id != selector.object_id:
            continue
        if selector.id_prefix is not None and not object_id.startswith(selector.id_prefix):
            continue
        yield kind, object_id, object_name, obj


def _iter_objects(
    model: GovernanceModel,
) -> Iterator[tuple[ObjectKind, str, str | None, object]]:
    for data_source in model.data_sources:
        yield "data_source", data_source.id, data_source.name, data_source
        for database in data_source.databases:
            yield "database", database.id, database.name, database
            for schema in database.schemas:
                yield "schema", schema.id, schema.name, schema
                for table in schema.tables:
                    yield "table", table.id, table.name, table
                    for column in table.columns:
                        yield "column", column.id, column.name, column
    for relationship in model.relationships:
        yield "relationship", relationship.id, relationship.name, relationship
