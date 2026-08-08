"""Normalized governance policy models (deterministic, path-independent)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PolicySeverity = Literal["error", "warning"]
RuleType = Literal["require_owner", "require_description", "require_relationship"]
ObjectKind = Literal[
    "data_source",
    "database",
    "schema",
    "table",
    "column",
    "relationship",
]

POLICY_SCHEMA = "governance-policy"
POLICY_VERSION = "1"

OWNER_KINDS: frozenset[str] = frozenset({"data_source", "database", "schema", "table"})
DESCRIPTION_KINDS: frozenset[str] = frozenset(
    {"data_source", "database", "schema", "table", "column", "relationship"}
)
RELATIONSHIP_KINDS: frozenset[str] = frozenset({"table"})


@dataclass(frozen=True, slots=True)
class PolicySelector:
    kind: ObjectKind
    object_id: str | None = None
    id_prefix: str | None = None

    def to_identity_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        if self.object_id is not None:
            payload["id"] = self.object_id
        if self.id_prefix is not None:
            payload["id_prefix"] = self.id_prefix
        return payload


@dataclass(frozen=True, slots=True)
class NormalizedPolicy:
    id: str
    severity: PolicySeverity
    rule_type: RuleType
    select: PolicySelector
    description: str | None = None

    def to_identity_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "rule": {
                "select": self.select.to_identity_dict(),
                "type": self.rule_type,
            },
            "severity": self.severity,
        }
        if self.description is not None:
            payload["description"] = self.description
        return payload


@dataclass(frozen=True, slots=True)
class NormalizedPolicySet:
    """Path-independent normalized policy set used for evaluation and identity."""

    policies: tuple[NormalizedPolicy, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policies",
            tuple(sorted(self.policies, key=lambda item: item.id)),
        )

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            "policies": [policy.to_identity_dict() for policy in self.policies],
            "policy_schema": POLICY_SCHEMA,
            "policy_version": POLICY_VERSION,
        }


@dataclass(frozen=True, slots=True)
class PolicyViolation:
    policy_id: str
    rule_type: RuleType
    severity: PolicySeverity
    object_kind: ObjectKind
    object_id: str
    reason: str
    object_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "object_id": self.object_id,
            "object_kind": self.object_kind,
            "policy_id": self.policy_id,
            "reason": self.reason,
            "rule_type": self.rule_type,
            "severity": self.severity,
        }
        if self.object_name is not None:
            payload["object_name"] = self.object_name
        return payload
