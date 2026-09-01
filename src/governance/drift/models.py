"""Drift classification models and constants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

DriftStatus = Literal["no_difference", "expected_difference", "unexpected_drift"]
DriftClassification = Literal["expected_difference", "unexpected_drift"]
DriftReason = Literal["matched_policy_rule", "unmatched_difference", "expectation_mismatch"]
DriftChange = Literal["added", "removed", "changed"]

DRIFT_SCHEMA = "governance-drift-result"
DRIFT_VERSION = "1"
DRIFT_POLICY_SCHEMA = "governance-drift-policy"
DRIFT_POLICY_VERSION = "1"
DIAGNOSTIC_SCHEMA = "governance-drift-diagnostics"
DIAGNOSTIC_VERSION = "1"

CHANGE_RANK: dict[str, int] = {"added": 0, "removed": 1, "changed": 2}
CLASSIFICATION_RANK: dict[str, int] = {"unexpected_drift": 0, "expected_difference": 1}

PRESENCE_ONLY = "__presence_only__"


@dataclass(frozen=True, slots=True)
class NormalizedDriftRule:
    id: str
    change: DriftChange
    object_identity: dict[str, Any]
    property: str | None
    expected: dict[str, Any] | None

    def selector_key(self) -> tuple[Any, ...]:
        identity_bytes = _identity_bytes(self.object_identity)
        return (self.change, identity_bytes, self.property)

    def expectation_projection(self) -> str:
        if self.expected is None:
            return PRESENCE_ONLY
        return _expected_projection_bytes(self.expected)

    def to_identity_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "change": self.change,
            "id": self.id,
            "object": self.object_identity,
        }
        if self.property is not None:
            payload["property"] = self.property
        if self.expected is not None:
            payload["expected"] = self.expected
        return payload


@dataclass(frozen=True, slots=True)
class NormalizedDriftPolicy:
    rules: tuple[NormalizedDriftRule, ...]

    def to_identity_dict(self) -> dict[str, Any]:
        sorted_rules = sorted(
            (rule.to_identity_dict() for rule in self.rules),
            key=_rule_identity_sort_key,
        )
        return {
            "drift_policy_schema": DRIFT_POLICY_SCHEMA,
            "drift_policy_version": DRIFT_POLICY_VERSION,
            "rules": sorted_rules,
        }


def _identity_bytes(identity: dict[str, Any]) -> bytes:
    from governance.identity.canonicalize import canonical_json_bytes

    return canonical_json_bytes(identity)


def _expected_projection_bytes(expected: dict[str, Any]) -> str:
    from governance.identity.canonicalize import canonical_json_bytes

    return canonical_json_bytes(expected).decode("ascii")


def _rule_identity_sort_key(rule: dict[str, Any]) -> tuple[Any, ...]:
    change = rule["change"]
    object_bytes = _identity_bytes(rule["object"])
    prop = rule.get("property", "")
    expected = rule.get("expected")
    expected_bytes = _expected_projection_bytes(expected) if expected is not None else ""
    return (CHANGE_RANK[change], object_bytes, prop, expected_bytes, rule["id"])
