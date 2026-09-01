"""Build governance-drift-result v1 artifacts."""

from __future__ import annotations

from typing import Any

from governance.drift.classify import classify_drift, derive_status
from governance.drift.models import DRIFT_SCHEMA, DRIFT_VERSION, NormalizedDriftPolicy
from governance.identity.hashing import drift_policy_identity, drift_result_identity


def build_drift_result(
    comparison: dict[str, Any],
    policy: NormalizedDriftPolicy | None,
) -> dict[str, Any]:
    comparison_identity = comparison["content_identity"]
    policy_identity_dict = None
    if policy is not None:
        policy_identity_dict = drift_policy_identity(policy.to_identity_dict()).to_dict()

    if comparison["status"] == "identical":
        return _build_no_difference_result(comparison_identity, policy_identity_dict)

    if policy is None:
        from governance.drift.errors import CODE_MISSING_DRIFT_POLICY, DiagnosticError, DriftError

        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_MISSING_DRIFT_POLICY,
                    path="/policy",
                    message="drift policy is required when comparison has differences",
                )
            ]
        )

    classified = classify_drift(comparison, policy)
    status = derive_status(classified)
    summary = _build_summary(classified)
    _assert_status_invariants(status, classified, summary)

    without_identity: dict[str, Any] = {
        "classified_changes": classified,
        "comparison_content_identity": comparison_identity,
        "drift_policy_identity": policy_identity_dict,
        "drift_schema": DRIFT_SCHEMA,
        "drift_version": DRIFT_VERSION,
        "status": status,
        "summary": summary,
        "writes_performed": 0,
    }
    identity = drift_result_identity(without_identity)
    return {**without_identity, "content_identity": identity.to_dict()}


def _build_no_difference_result(
    comparison_identity: dict[str, Any],
    policy_identity_dict: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = {
        "affected_objects": 0,
        "expected_differences": 0,
        "property_drift": 0,
        "unexpected_drift": 0,
    }
    without_identity: dict[str, Any] = {
        "classified_changes": [],
        "comparison_content_identity": comparison_identity,
        "drift_policy_identity": policy_identity_dict,
        "drift_schema": DRIFT_SCHEMA,
        "drift_version": DRIFT_VERSION,
        "status": "no_difference",
        "summary": summary,
        "writes_performed": 0,
    }
    identity = drift_result_identity(without_identity)
    return {**without_identity, "content_identity": identity.to_dict()}


def _build_summary(classified: list[dict[str, Any]]) -> dict[str, int]:
    expected = sum(1 for item in classified if item["classification"] == "expected_difference")
    unexpected = sum(1 for item in classified if item["classification"] == "unexpected_drift")
    affected = len({canonical_object_key(item["object_identity"]) for item in classified})
    property_drift = sum(
        1
        for item in classified
        if item["classification"] == "unexpected_drift" and item["change"] == "changed"
    )
    return {
        "affected_objects": affected,
        "expected_differences": expected,
        "property_drift": property_drift,
        "unexpected_drift": unexpected,
    }


def canonical_object_key(object_identity: dict[str, Any]) -> bytes:
    from governance.identity.canonicalize import canonical_json_bytes

    return canonical_json_bytes(object_identity)


def _assert_status_invariants(
    status: str,
    classified: list[dict[str, Any]],
    summary: dict[str, int],
) -> None:
    expected = summary["expected_differences"]
    unexpected = summary["unexpected_drift"]
    if expected + unexpected != len(classified):
        raise ValueError("summary counts do not match classified_changes length")
    if status == "expected_difference" and (not classified or unexpected != 0):
        raise ValueError("expected_difference requires classified changes without unexpected drift")
    if status == "unexpected_drift" and unexpected <= 0:
        raise ValueError("unexpected_drift requires unexpected summary count")
