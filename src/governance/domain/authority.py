"""Domain authority models for metadata property authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from governance.domain.graph import (
    NODE_KIND_COLUMN,
    NODE_KIND_CONTRACT,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    NODE_KIND_TRANSFORMATION,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.observations import PropertyPath
from governance.identity.canonicalize import canonical_json_bytes
from governance.identity.hashing import ContentIdentity, authority_identity

AUTHORITY_SCHEMA = "governance-authority"
AUTHORITY_VERSION = "1"

AUTHORITY_NODE_KINDS: tuple[str, ...] = (
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    NODE_KIND_COLUMN,
    NODE_KIND_CONTRACT,
    NODE_KIND_TRANSFORMATION,
)

_AUTHORITY_NODE_KIND_SET = frozenset(AUTHORITY_NODE_KINDS)


def _require_non_empty_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


@dataclass(frozen=True, slots=True)
class AuthoritySelector:
    """Exact property selector (optional namespace)."""

    kind: str
    property_path: PropertyPath
    namespace: str | None = None

    def __post_init__(self) -> None:
        kind = _require_non_empty_str(self.kind, "kind")
        if kind not in _AUTHORITY_NODE_KIND_SET:
            raise ValueError(f"unsupported authority kind: {kind}")
        object.__setattr__(self, "kind", kind)
        if not isinstance(self.property_path, PropertyPath):
            raise TypeError("property_path must be PropertyPath")
        if self.namespace is not None:
            object.__setattr__(
                self,
                "namespace",
                _require_non_empty_str(self.namespace, "namespace"),
            )

    @property
    def specificity_rank(self) -> int:
        return 2 if self.namespace is not None else 1

    def matches(self, identity: GraphNodeIdentity, property_path: PropertyPath) -> bool:
        if identity.kind != self.kind:
            return False
        if property_path != self.property_path:
            return False
        return self.namespace is None or identity.namespace == self.namespace

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "property": self.property_path.to_pointer(),
        }
        if self.namespace is not None:
            payload["namespace"] = self.namespace
        return payload


@dataclass(frozen=True, slots=True)
class AuthorityTarget:
    """Authoritative provenance filter (provider required; source_ref optional)."""

    provider_type: str
    source_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_type",
            _require_non_empty_str(self.provider_type, "provider_type"),
        )
        if self.source_ref is not None:
            object.__setattr__(
                self,
                "source_ref",
                _require_non_empty_str(self.source_ref, "source_ref"),
            )

    def matches(self, record: ProvenanceRecord) -> bool:
        if record.provider_type != self.provider_type:
            return False
        return self.source_ref is None or record.source_ref == self.source_ref

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"provider_type": self.provider_type}
        if self.source_ref is not None:
            payload["source_ref"] = self.source_ref
        return payload


@dataclass(frozen=True, slots=True)
class AuthorityRuleKey:
    """Semantic authority rule identity (no YAML id / description)."""

    selector: AuthoritySelector
    authority: AuthorityTarget

    def __post_init__(self) -> None:
        if not isinstance(self.selector, AuthoritySelector):
            raise TypeError("selector must be AuthoritySelector")
        if not isinstance(self.authority, AuthorityTarget):
            raise TypeError("authority must be AuthorityTarget")

    def to_dict(self) -> dict[str, Any]:
        return {
            "authority": self.authority.to_dict(),
            "select": self.selector.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class AuthorityDeclaration:
    """Audit-only declaration metadata for a semantic rule."""

    config_id: str
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "config_id",
            _require_non_empty_str(self.config_id, "config_id"),
        )
        if self.description is not None:
            object.__setattr__(
                self,
                "description",
                _require_non_empty_str(self.description, "description"),
            )

    def sort_key(self) -> tuple[str, str]:
        return (self.config_id, self.description or "")


@dataclass(frozen=True, slots=True)
class NormalizedAuthorityRule:
    """One semantic rule key with retained audit declarations."""

    key: AuthorityRuleKey
    declarations: tuple[AuthorityDeclaration, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.key, AuthorityRuleKey):
            raise TypeError("key must be AuthorityRuleKey")
        if not self.declarations:
            raise ValueError("NormalizedAuthorityRule.declarations must be non-empty")
        unique: dict[AuthorityDeclaration, None] = {}
        for declaration in self.declarations:
            if not isinstance(declaration, AuthorityDeclaration):
                raise TypeError("declarations must be AuthorityDeclaration")
            unique[declaration] = None
        ordered = tuple(sorted(unique.keys(), key=lambda item: item.sort_key()))
        object.__setattr__(self, "declarations", ordered)


@dataclass(frozen=True, slots=True)
class NormalizedAuthorityPolicySet:
    """Deterministic normalized authority rule set."""

    rules: tuple[NormalizedAuthorityRule, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rules",
            tuple(sorted(self.rules, key=lambda rule: rule.key.canonical_bytes())),
        )

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            "authority_schema": AUTHORITY_SCHEMA,
            "authority_version": AUTHORITY_VERSION,
            "rules": [rule.key.to_dict() for rule in self.rules],
        }

    def content_identity(self) -> ContentIdentity:
        return authority_identity(self.to_identity_dict())

    def matching_rules(
        self,
        identity: GraphNodeIdentity,
        property_path: PropertyPath,
    ) -> tuple[NormalizedAuthorityRule, ...]:
        return tuple(
            rule for rule in self.rules if rule.key.selector.matches(identity, property_path)
        )
