"""Build, validate, and persist governance-ci-review-result v1 artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from governance.domain.conflicts import PropertyConflictReport, PropertyConflictResult
from governance.identity.hashing import ContentIdentity, ci_review_result_identity
from governance.identity.json_values import validate_json_value
from governance.io.atomic import atomic_write_text
from governance.reconciliation.safety import ReconciliationAssessment

REVIEW_SCHEMA = "governance-ci-review-result"
REVIEW_VERSION = "1"
REVIEW_RESULT_NAME = "review-result.json"
COMPARISON_RESULT_NAME = "comparison-result.json"
DRIFT_RESULT_NAME = "drift-result.json"

ReviewResultErrorCode = Literal[
    "read_error",
    "parse_error",
    "invalid_artifact",
    "unsupported_schema",
    "unsupported_version",
    "integrity_mismatch",
    "write_error",
]

_CONTENT_IDENTITY_KEYS = frozenset({"algorithm", "hashing_contract_version", "digest"})


@dataclass(frozen=True, slots=True)
class ReviewResultDiagnostic:
    code: ReviewResultErrorCode
    path: str
    message: str


class ReviewResultError(RuntimeError):
    """Bounded review-result load/validation failure (no host paths / tracebacks)."""

    def __init__(self, errors: list[ReviewResultDiagnostic]) -> None:
        if not errors:
            raise ValueError("ReviewResultError requires at least one diagnostic")
        self.errors = list(errors)
        super().__init__(errors[0].message)


def empty_conflict_summary() -> dict[str, int]:
    return {
        "properties_analyzed": 0,
        "single_observation": 0,
        "agreement": 0,
        "resolved_by_authority": 0,
        "unresolved_conflict": 0,
        "invalid_or_ambiguous_authority": 0,
        "reconciliation_safe": 0,
        "reconciliation_blocked": 0,
        "reconciliation_not_applicable": 0,
    }


def empty_conflicts_block() -> dict[str, Any]:
    return {
        "status": "not_run",
        "observations_content_identity": None,
        "authority_content_identity": None,
        "conflict_report_content_identity": None,
        "summary": empty_conflict_summary(),
        "conflict_report": None,
        "reconciliation_assessments": [],
    }


def empty_drift_block() -> dict[str, Any]:
    return {
        "status": "not_run",
        "baseline_snapshot_identity": None,
        "candidate_snapshot_identity": None,
        "comparison_content_identity": None,
        "drift_policy_identity": None,
        "drift_result_content_identity": None,
        "summary": None,
    }


def _assessment_entry(
    result: PropertyConflictResult,
    assessment: ReconciliationAssessment,
) -> dict[str, Any]:
    return {
        "object": result.object_identity.to_dict(),
        "property": result.property_path.to_pointer(),
        "applicable": assessment.applicable,
        "safe": assessment.safe,
        "reason": assessment.reason,
    }


def build_conflicts_block(
    *,
    report: PropertyConflictReport,
    assessments: list[ReconciliationAssessment],
    observations_content_identity: dict[str, str],
    authority_content_identity: dict[str, str],
) -> dict[str, Any]:
    """Build the conflicts lane from domain SoT results (no resolution logic)."""
    if len(assessments) != len(report.results):
        raise ValueError("assessments must align 1:1 with conflict report results")

    summary = empty_conflict_summary()
    summary["properties_analyzed"] = len(report.results)
    for result, assessment in zip(report.results, assessments, strict=True):
        if result.state == "SINGLE_OBSERVATION":
            summary["single_observation"] += 1
        elif result.state == "AGREEMENT":
            summary["agreement"] += 1
        elif result.state == "RESOLVED_BY_AUTHORITY":
            summary["resolved_by_authority"] += 1
        elif result.state == "UNRESOLVED_CONFLICT":
            summary["unresolved_conflict"] += 1
        elif result.state == "INVALID_OR_AMBIGUOUS_AUTHORITY":
            summary["invalid_or_ambiguous_authority"] += 1

        if not assessment.applicable:
            summary["reconciliation_not_applicable"] += 1
        elif assessment.safe:
            summary["reconciliation_safe"] += 1
        else:
            summary["reconciliation_blocked"] += 1

    finding_count = summary["unresolved_conflict"] + summary["invalid_or_ambiguous_authority"]
    if summary["reconciliation_blocked"] > 0:
        status = "blocked"
    elif finding_count > 0:
        status = "findings"
    else:
        status = "clear"

    report_identity = report.content_identity().to_dict()
    return {
        "status": status,
        "observations_content_identity": dict(observations_content_identity),
        "authority_content_identity": dict(authority_content_identity),
        "conflict_report_content_identity": report_identity,
        "summary": summary,
        "conflict_report": report.to_identity_dict(),
        "reconciliation_assessments": [
            _assessment_entry(result, assessment)
            for result, assessment in zip(report.results, assessments, strict=True)
        ],
    }


def build_drift_block(
    *,
    comparison: dict[str, Any],
    drift_result: dict[str, Any],
) -> dict[str, Any]:
    """Derive the drift lane from verified comparison + drift-result artifacts."""
    baseline = comparison.get("baseline")
    candidate = comparison.get("candidate")
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise ValueError("comparison must include baseline and candidate metadata")
    summary = drift_result.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("drift_result must include summary")
    return {
        "status": str(drift_result["status"]),
        "baseline_snapshot_identity": dict(baseline["content_identity"]),
        "candidate_snapshot_identity": dict(candidate["content_identity"]),
        "comparison_content_identity": dict(drift_result["comparison_content_identity"]),
        "drift_policy_identity": (
            None
            if drift_result.get("drift_policy_identity") is None
            else dict(drift_result["drift_policy_identity"])
        ),
        "drift_result_content_identity": dict(drift_result["content_identity"]),
        "summary": {
            "affected_objects": int(summary["affected_objects"]),
            "expected_differences": int(summary["expected_differences"]),
            "property_drift": int(summary["property_drift"]),
            "unexpected_drift": int(summary["unexpected_drift"]),
        },
    }


def derive_review_status(*, conflicts: dict[str, Any], drift: dict[str, Any]) -> str:
    if conflicts.get("status") == "blocked" or drift.get("status") == "unexpected_drift":
        return "blocked"
    return "passed"


def build_review_result(
    *,
    conflicts: dict[str, Any] | None = None,
    drift: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete governance-ci-review-result v1 including content_identity."""
    conflicts_block = empty_conflicts_block() if conflicts is None else conflicts
    drift_block = empty_drift_block() if drift is None else drift
    status = derive_review_status(conflicts=conflicts_block, drift=drift_block)
    without_identity: dict[str, Any] = {
        "conflicts": conflicts_block,
        "drift": drift_block,
        "review_schema": REVIEW_SCHEMA,
        "review_version": REVIEW_VERSION,
        "status": status,
        "writes_performed": 0,
    }
    identity = ci_review_result_identity(without_identity)
    return {**without_identity, "content_identity": identity.to_dict()}


def canonical_review_json(result: dict[str, Any]) -> str:
    return (
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    )


def build_review_console_json(
    *,
    status: str,
    review_status: str,
    conflict_status: str,
    drift_status: str,
    conflict_property_count: str | int,
    unresolved_conflict_count: str | int,
    resolved_authority_count: str | int,
    reconciliation_blocked_count: str | int,
    expected_difference_count: str | int,
    unexpected_drift_count: str | int,
    drift_affected_object_count: str | int,
    review_result_path: str,
    review_result_version: str,
    comparison_result_path: str,
    drift_result_path: str,
    failure_code: str | None = None,
) -> str:
    """Safe deterministic console JSON for Action logs (no machine conflict detail)."""
    payload: dict[str, Any] = {
        "comparison_result_path": str(comparison_result_path or ""),
        "conflict_property_count": int(conflict_property_count),
        "conflict_status": str(conflict_status),
        "drift_affected_object_count": int(drift_affected_object_count),
        "drift_result_path": str(drift_result_path or ""),
        "drift_status": str(drift_status),
        "expected_difference_count": int(expected_difference_count),
        "operation": "review",
        "reconciliation_blocked_count": int(reconciliation_blocked_count),
        "resolved_authority_count": int(resolved_authority_count),
        "review_result_path": str(review_result_path or ""),
        "review_result_version": str(review_result_version or ""),
        "review_status": str(review_status),
        "status": str(status),
        "unexpected_drift_count": int(unexpected_drift_count),
        "unresolved_conflict_count": int(unresolved_conflict_count),
        "writes_performed": 0,
    }
    if failure_code:
        payload["failure_code"] = str(failure_code)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def write_review_result(result: dict[str, Any], path: str | Path) -> Path:
    """Validate then atomically write a governance-ci-review-result v1 artifact."""
    validate_review_result(result)
    target = Path(path)
    try:
        return atomic_write_text(target, canonical_review_json(result))
    except OSError as exc:
        raise ReviewResultError(
            [
                ReviewResultDiagnostic(
                    code="write_error",
                    path="/output",
                    message="unable to write review-result artifact",
                )
            ]
        ) from exc


def _invalid(path: str, message: str) -> ReviewResultError:
    return ReviewResultError(
        [ReviewResultDiagnostic(code="invalid_artifact", path=path, message=message)]
    )


def _require_identity(block: dict[str, Any], key: str, *, lane: str) -> dict[str, Any]:
    value = block.get(key)
    if not isinstance(value, dict):
        raise _invalid(f"/{lane}/{key}", f"active {lane} lane requires {key}")
    return value


def _derive_conflict_status(summary: dict[str, int]) -> str:
    if summary["reconciliation_blocked"] > 0:
        return "blocked"
    finding_count = summary["unresolved_conflict"] + summary["invalid_or_ambiguous_authority"]
    if finding_count > 0:
        return "findings"
    return "clear"


def _assert_active_conflicts(conflicts: dict[str, Any]) -> None:
    from governance.identity.hashing import property_conflict_report_identity

    for key in (
        "observations_content_identity",
        "authority_content_identity",
        "conflict_report_content_identity",
    ):
        _require_identity(conflicts, key, lane="conflicts")

    report = conflicts.get("conflict_report")
    if not isinstance(report, dict):
        raise _invalid(
            "/conflicts/conflict_report",
            "active conflicts lane requires conflict_report",
        )
    if report.get("conflict_schema") != "governance-property-conflicts":
        raise _invalid(
            "/conflicts/conflict_report/conflict_schema",
            "conflict_report must declare governance-property-conflicts",
        )
    if report.get("conflict_version") != "1":
        raise _invalid(
            "/conflicts/conflict_report/conflict_version",
            "conflict_report must declare version 1",
        )

    results = report.get("results")
    assessments = conflicts.get("reconciliation_assessments")
    summary = conflicts.get("summary")
    if not isinstance(results, list):
        raise _invalid(
            "/conflicts/conflict_report/results",
            "conflict_report.results must be a list",
        )
    if not isinstance(assessments, list):
        raise _invalid(
            "/conflicts/reconciliation_assessments",
            "reconciliation_assessments must be a list",
        )
    if not isinstance(summary, dict):
        raise _invalid("/conflicts/summary", "conflicts summary must be an object")

    if len(assessments) != len(results):
        raise _invalid(
            "/conflicts/reconciliation_assessments",
            "reconciliation_assessments must align 1:1 with conflict_report.results",
        )
    if int(summary.get("properties_analyzed", -1)) != len(results):
        raise _invalid(
            "/conflicts/summary/properties_analyzed",
            "properties_analyzed must equal conflict_report.results length",
        )

    state_counts = {
        "single_observation": 0,
        "agreement": 0,
        "resolved_by_authority": 0,
        "unresolved_conflict": 0,
        "invalid_or_ambiguous_authority": 0,
    }
    recon_safe = 0
    recon_blocked = 0
    recon_na = 0
    for index, (result, assessment) in enumerate(zip(results, assessments, strict=True)):
        if not isinstance(result, dict) or not isinstance(assessment, dict):
            raise _invalid(
                f"/conflicts/reconciliation_assessments/{index}",
                "assessment/result entries must be objects",
            )
        if assessment.get("object") != result.get("object"):
            raise _invalid(
                f"/conflicts/reconciliation_assessments/{index}/object",
                "assessment object must match conflict result object",
            )
        if assessment.get("property") != result.get("property"):
            raise _invalid(
                f"/conflicts/reconciliation_assessments/{index}/property",
                "assessment property must match conflict result property",
            )
        state = result.get("state")
        if state == "SINGLE_OBSERVATION":
            state_counts["single_observation"] += 1
        elif state == "AGREEMENT":
            state_counts["agreement"] += 1
        elif state == "RESOLVED_BY_AUTHORITY":
            state_counts["resolved_by_authority"] += 1
        elif state == "UNRESOLVED_CONFLICT":
            state_counts["unresolved_conflict"] += 1
        elif state == "INVALID_OR_AMBIGUOUS_AUTHORITY":
            state_counts["invalid_or_ambiguous_authority"] += 1
        else:
            raise _invalid(
                f"/conflicts/conflict_report/results/{index}/state",
                "unrecognized conflict state",
            )

        if not bool(assessment.get("applicable")):
            recon_na += 1
        elif bool(assessment.get("safe")):
            recon_safe += 1
        else:
            recon_blocked += 1

    for key, expected in state_counts.items():
        if int(summary.get(key, -1)) != expected:
            raise _invalid(
                f"/conflicts/summary/{key}",
                f"conflicts summary.{key} does not match conflict_report.results",
            )
    if int(summary.get("reconciliation_safe", -1)) != recon_safe:
        raise _invalid(
            "/conflicts/summary/reconciliation_safe",
            "reconciliation_safe does not match assessments",
        )
    if int(summary.get("reconciliation_blocked", -1)) != recon_blocked:
        raise _invalid(
            "/conflicts/summary/reconciliation_blocked",
            "reconciliation_blocked does not match assessments",
        )
    if int(summary.get("reconciliation_not_applicable", -1)) != recon_na:
        raise _invalid(
            "/conflicts/summary/reconciliation_not_applicable",
            "reconciliation_not_applicable does not match assessments",
        )
    if recon_safe + recon_blocked + recon_na != len(results):
        raise _invalid(
            "/conflicts/summary",
            "reconciliation counts must sum to properties_analyzed",
        )

    expected_status = _derive_conflict_status(
        {
            "reconciliation_blocked": recon_blocked,
            "unresolved_conflict": state_counts["unresolved_conflict"],
            "invalid_or_ambiguous_authority": state_counts["invalid_or_ambiguous_authority"],
        }
    )
    if conflicts.get("status") != expected_status:
        raise _invalid(
            "/conflicts/status",
            "conflicts.status contradicts summary/assessment semantics",
        )

    try:
        expected_identity = property_conflict_report_identity(report).to_dict()
    except RecursionError as exc:
        raise _invalid("/", "review-result artifact is too deeply nested") from exc
    if conflicts.get("conflict_report_content_identity") != expected_identity:
        raise _invalid(
            "/conflicts/conflict_report_content_identity",
            "conflict_report_content_identity mismatch",
        )


def _assert_active_drift(drift: dict[str, Any]) -> None:
    for key in (
        "baseline_snapshot_identity",
        "candidate_snapshot_identity",
        "comparison_content_identity",
        "drift_result_content_identity",
    ):
        _require_identity(drift, key, lane="drift")

    summary = drift.get("summary")
    if not isinstance(summary, dict):
        raise _invalid("/drift/summary", "active drift lane requires summary")

    status = drift.get("status")
    policy_identity = drift.get("drift_policy_identity")
    if status in {"expected_difference", "unexpected_drift"}:
        if not isinstance(policy_identity, dict):
            raise _invalid(
                "/drift/drift_policy_identity",
                f"{status} requires non-null drift_policy_identity",
            )
    elif status == "no_difference":
        if policy_identity is not None and not isinstance(policy_identity, dict):
            raise _invalid(
                "/drift/drift_policy_identity",
                "drift_policy_identity must be null or ContentIdentity",
            )
    else:
        raise _invalid("/drift/status", "unrecognized active drift status")

    counts = {
        "affected_objects": int(summary.get("affected_objects", -1)),
        "expected_differences": int(summary.get("expected_differences", -1)),
        "property_drift": int(summary.get("property_drift", -1)),
        "unexpected_drift": int(summary.get("unexpected_drift", -1)),
    }
    if any(value < 0 for value in counts.values()):
        raise _invalid("/drift/summary", "drift summary counts must be non-negative integers")

    if status == "no_difference":
        if any(counts[key] != 0 for key in counts):
            raise _invalid(
                "/drift/summary",
                "no_difference requires all drift summary counts to be 0",
            )
    elif status == "expected_difference":
        if counts["expected_differences"] <= 0 or counts["unexpected_drift"] != 0:
            raise _invalid(
                "/drift/summary",
                "expected_difference requires expected_differences>0 and unexpected_drift==0",
            )
    elif status == "unexpected_drift" and counts["unexpected_drift"] <= 0:
        raise _invalid(
            "/drift/summary/unexpected_drift",
            "unexpected_drift requires unexpected_drift>0",
        )


def _schema_diagnostic(error: Any) -> ReviewResultDiagnostic:
    path = "/" + "/".join(str(part) for part in error.absolute_path)
    if path == "/":
        path = "/"
    return ReviewResultDiagnostic(
        code="invalid_artifact",
        path=path or "/",
        message="review-result schema validation failed",
    )


def _verify_content_identity(payload: dict[str, Any]) -> None:
    identity_raw = payload.get("content_identity")
    if not isinstance(identity_raw, dict):
        raise _invalid("/content_identity", "review-result content_identity must be an object")
    if set(identity_raw.keys()) != _CONTENT_IDENTITY_KEYS:
        raise _invalid("/content_identity", "review-result content_identity keys invalid")
    try:
        stored = ContentIdentity(
            algorithm=str(identity_raw["algorithm"]),
            hashing_contract_version=str(identity_raw["hashing_contract_version"]),
            digest=str(identity_raw["digest"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid("/content_identity", "review-result content_identity malformed") from exc

    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    try:
        expected = ci_review_result_identity(without_identity)
    except RecursionError as exc:
        raise _invalid("/", "review-result artifact is too deeply nested") from exc
    if (
        stored.algorithm != expected.algorithm
        or stored.hashing_contract_version != expected.hashing_contract_version
        or stored.digest != expected.digest
    ):
        raise ReviewResultError(
            [
                ReviewResultDiagnostic(
                    code="integrity_mismatch",
                    path="/content_identity",
                    message="review-result content_identity mismatch",
                )
            ]
        )


def validate_review_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Schema-validate and verify identity of an in-memory review-result."""
    if not isinstance(payload, dict):
        raise ReviewResultError(
            [
                ReviewResultDiagnostic(
                    code="parse_error",
                    path="/",
                    message="review-result root must be a mapping",
                )
            ]
        )

    try:
        validate_json_value(payload)
    except (TypeError, ValueError, RecursionError) as exc:
        raise _invalid("/", "review-result contains non-finite or invalid JSON values") from exc

    schema_name = payload.get("review_schema")
    version = payload.get("review_version")
    if schema_name != REVIEW_SCHEMA:
        raise ReviewResultError(
            [
                ReviewResultDiagnostic(
                    code="unsupported_schema",
                    path="/review_schema",
                    message="unsupported review-result schema",
                )
            ]
        )
    if version != REVIEW_VERSION:
        raise ReviewResultError(
            [
                ReviewResultDiagnostic(
                    code="unsupported_version",
                    path="/review_version",
                    message="unsupported review-result version",
                )
            ]
        )

    schema_text = (
        files("governance.github_ci.schemas")
        .joinpath("governance-ci-review-result.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    validator = Draft202012Validator(schema)
    try:
        schema_errors = sorted(
            validator.iter_errors(payload),
            key=lambda item: (
                "/" + "/".join(str(part) for part in item.absolute_path),
                item.validator,
            ),
        )
    except RecursionError as exc:
        raise _invalid("/", "review-result artifact is too deeply nested") from exc
    if schema_errors:
        raise ReviewResultError([_schema_diagnostic(error) for error in schema_errors])

    _verify_content_identity(payload)
    _assert_lane_invariants(payload)
    return payload


def _assert_lane_invariants(payload: dict[str, Any]) -> None:
    conflicts = payload["conflicts"]
    drift = payload["drift"]
    expected = derive_review_status(conflicts=conflicts, drift=drift)
    if payload["status"] != expected:
        raise _invalid("/status", "review-result status inconsistent with lane statuses")
    if payload.get("writes_performed") != 0:
        raise _invalid("/writes_performed", "review-result writes_performed must be 0")

    if conflicts["status"] == "not_run":
        if conflicts["conflict_report"] is not None or conflicts["reconciliation_assessments"]:
            raise _invalid("/conflicts", "not_run conflicts lane must be empty")
        for key in (
            "observations_content_identity",
            "authority_content_identity",
            "conflict_report_content_identity",
        ):
            if conflicts[key] is not None:
                raise _invalid(
                    f"/conflicts/{key}",
                    "not_run conflicts identities must be null",
                )
        summary = conflicts.get("summary")
        if isinstance(summary, dict) and any(int(summary.get(k, 0)) != 0 for k in summary):
            raise _invalid("/conflicts/summary", "not_run conflicts summary counts must be 0")
    else:
        _assert_active_conflicts(conflicts)

    if drift["status"] == "not_run":
        if drift["summary"] is not None:
            raise _invalid("/drift/summary", "not_run drift summary must be null")
        for key in (
            "baseline_snapshot_identity",
            "candidate_snapshot_identity",
            "comparison_content_identity",
            "drift_policy_identity",
            "drift_result_content_identity",
        ):
            if drift[key] is not None:
                raise _invalid(f"/drift/{key}", "not_run drift identities must be null")
    else:
        _assert_active_drift(drift)


def load_review_result_artifact(path: str | Path) -> dict[str, Any]:
    """Strict-load an external governance-ci-review-result v1 artifact."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ReviewResultError(
            [
                ReviewResultDiagnostic(
                    code="parse_error",
                    path="/",
                    message="invalid review-result JSON",
                )
            ]
        ) from exc
    except OSError as exc:
        raise ReviewResultError(
            [
                ReviewResultDiagnostic(
                    code="read_error",
                    path="/",
                    message="unable to read review-result artifact",
                )
            ]
        ) from exc

    def _reject_non_standard_json(_value: str) -> None:
        raise ValueError("non-standard JSON literal")

    def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            text,
            parse_constant=_reject_non_standard_json,
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ReviewResultError(
            [
                ReviewResultDiagnostic(
                    code="parse_error",
                    path="/",
                    message="invalid review-result JSON",
                )
            ]
        ) from exc

    try:
        validate_json_value(payload)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ReviewResultError(
            [
                ReviewResultDiagnostic(
                    code="parse_error",
                    path="/",
                    message="invalid review-result JSON",
                )
            ]
        ) from exc

    return validate_review_result(payload)


def desired_review_exit_code(*, status: str, fail_on_review_blocked: bool) -> int:
    if status == "passed":
        return 0
    if status == "blocked":
        return 3 if fail_on_review_blocked else 0
    return 1


__all__ = [
    "COMPARISON_RESULT_NAME",
    "DRIFT_RESULT_NAME",
    "REVIEW_RESULT_NAME",
    "REVIEW_SCHEMA",
    "REVIEW_VERSION",
    "ReviewResultDiagnostic",
    "ReviewResultError",
    "build_conflicts_block",
    "build_drift_block",
    "build_review_console_json",
    "build_review_result",
    "canonical_review_json",
    "desired_review_exit_code",
    "empty_conflicts_block",
    "empty_drift_block",
    "load_review_result_artifact",
    "validate_review_result",
    "write_review_result",
]
