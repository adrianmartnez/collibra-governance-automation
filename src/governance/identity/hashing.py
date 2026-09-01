"""SHA-256 content identities with domain separation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from governance.identity.canonicalize import canonical_json_bytes

HASHING_CONTRACT_VERSION = "1"
ALGORITHM = "sha256"

_PREFIX_CONFIG = b"gov-config-v1\n"
_PREFIX_SNAPSHOT = b"gov-snapshot-v1\n"
_PREFIX_MAPPING = b"gov-mapping-v1\n"
_PREFIX_POLICY = b"gov-policy-v1\n"
_PREFIX_AUTHORITY = b"gov-authority-v1\n"
_PREFIX_PROPERTY_OBSERVATIONS = b"gov-property-observations-v1\n"
_PREFIX_PROPERTY_CONFLICTS = b"gov-property-conflicts-v1\n"
_PREFIX_REMOTE_STATE = b"gov-remote-state-v1\n"
_PREFIX_TARGET_CONTEXT = b"gov-target-context-v1\n"
_PREFIX_PLAN = b"gov-plan-v1\n"
_PREFIX_PLAN_V2 = b"gov-plan-v2\n"
_PREFIX_GRAPH = b"gov-graph-v1\n"
_PREFIX_IMPACT_RESULT = b"gov-impact-result-v1\n"
_PREFIX_RECONCILIATION_ASSUMPTIONS = b"gov-reconciliation-assumptions-v1\n"
_PREFIX_EXPLAIN_RESULT = b"gov-explain-result-v1\n"


@dataclass(frozen=True, slots=True)
class ContentIdentity:
    """Machine-readable integrity identity (not authenticity)."""

    algorithm: str
    hashing_contract_version: str
    digest: str

    def to_dict(self) -> dict[str, str]:
        return {
            "algorithm": self.algorithm,
            "digest": self.digest,
            "hashing_contract_version": self.hashing_contract_version,
        }


def _sha256_identity(prefix: bytes, payload: bytes) -> ContentIdentity:
    digest = hashlib.sha256(prefix + payload).hexdigest()
    return ContentIdentity(
        algorithm=ALGORITHM,
        hashing_contract_version=HASHING_CONTRACT_VERSION,
        digest=digest,
    )


def config_identity(identity_projection: dict[str, Any]) -> ContentIdentity:
    """Identity for the governance-relevant projection of CanonicalConfig."""
    return _sha256_identity(_PREFIX_CONFIG, canonical_json_bytes(identity_projection))


def snapshot_identity(canonical_snapshot_without_identity: dict[str, Any]) -> ContentIdentity:
    """Identity for canonical snapshot content excluding content_identity."""
    return _sha256_identity(
        _PREFIX_SNAPSHOT,
        canonical_json_bytes(canonical_snapshot_without_identity),
    )


def mapping_identity(normalized_mapping: dict[str, Any]) -> ContentIdentity:
    """Identity for normalized mapping content (not file path)."""
    return _sha256_identity(_PREFIX_MAPPING, canonical_json_bytes(normalized_mapping))


def policy_identity(normalized_policy_set: dict[str, Any]) -> ContentIdentity:
    """Identity for normalized policy semantics (not file paths)."""
    return _sha256_identity(_PREFIX_POLICY, canonical_json_bytes(normalized_policy_set))


def authority_identity(normalized_authority_set: dict[str, Any]) -> ContentIdentity:
    """Identity for normalized authority semantics (not file paths)."""
    return _sha256_identity(_PREFIX_AUTHORITY, canonical_json_bytes(normalized_authority_set))


def property_observation_set_identity(observation_set: dict[str, Any]) -> ContentIdentity:
    """Identity for normalized property observation set payload."""
    return _sha256_identity(
        _PREFIX_PROPERTY_OBSERVATIONS,
        canonical_json_bytes(observation_set),
    )


def property_conflict_report_identity(conflict_report: dict[str, Any]) -> ContentIdentity:
    """Identity for normalized property conflict report payload."""
    return _sha256_identity(
        _PREFIX_PROPERTY_CONFLICTS,
        canonical_json_bytes(conflict_report),
    )


def remote_state_identity(managed_remote_projection: dict[str, Any]) -> ContentIdentity:
    """Identity for managed remote state material to reconciliation."""
    return _sha256_identity(
        _PREFIX_REMOTE_STATE,
        canonical_json_bytes(managed_remote_projection),
    )


def target_context_identity(target_context_projection: dict[str, Any]) -> ContentIdentity:
    """Identity for effective Collibra destination context (no secrets)."""
    return _sha256_identity(
        _PREFIX_TARGET_CONTEXT,
        canonical_json_bytes(target_context_projection),
    )


def plan_identity(
    canonical_plan_without_identity: dict[str, Any],
    *,
    plan_version: str = "1",
) -> ContentIdentity:
    """Identity for saved plan payload excluding content_identity."""
    if plan_version == "1":
        prefix = _PREFIX_PLAN
    elif plan_version == "2":
        prefix = _PREFIX_PLAN_V2
    else:
        raise ValueError(f"unsupported plan_version for plan_identity: {plan_version!r}")
    return _sha256_identity(
        prefix,
        canonical_json_bytes(canonical_plan_without_identity),
    )


def reconciliation_assumptions_identity(
    assumptions_root: dict[str, Any],
) -> ContentIdentity:
    """Identity for governance-reconciliation-assumptions v1 root."""
    return _sha256_identity(
        _PREFIX_RECONCILIATION_ASSUMPTIONS,
        canonical_json_bytes(assumptions_root),
    )


def explain_result_identity(
    canonical_result_without_identity: dict[str, Any],
) -> ContentIdentity:
    """Identity for governance-explain-result payload excluding content_identity."""
    return _sha256_identity(
        _PREFIX_EXPLAIN_RESULT,
        canonical_json_bytes(canonical_result_without_identity),
    )


def graph_identity(canonical_graph_without_identity: dict[str, Any]) -> ContentIdentity:
    """Identity for canonical governance graph content excluding content_identity.

    Callers should pass ``GovernanceGraph.canonical_dict_without_identity()``.
    This helper hashes the provided projection; it does not validate graph structure.
    """
    return _sha256_identity(
        _PREFIX_GRAPH,
        canonical_json_bytes(canonical_graph_without_identity),
    )


def impact_result_identity(canonical_result_without_identity: dict[str, Any]) -> ContentIdentity:
    """Identity for impact result payload excluding content_identity."""
    return _sha256_identity(
        _PREFIX_IMPACT_RESULT,
        canonical_json_bytes(canonical_result_without_identity),
    )
