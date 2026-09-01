"""Build canonical governance-snapshot-comparison v1 results."""

from __future__ import annotations

from typing import Any

from governance.comparison.align import (
    RootAlignmentAck,
    RootAlignmentResult,
    resolve_root_alignment,
)
from governance.comparison.compare import compare_projected
from governance.comparison.errors import (
    CODE_SCANNER_MISMATCH,
    CODE_SYSTEM_TYPE_MISMATCH,
    ComparisonError,
    DiagnosticError,
)
from governance.comparison.projection import project_snapshot
from governance.identity.hashing import snapshot_comparison_identity
from governance.snapshots.models import GovernanceSnapshot

COMPARISON_SCHEMA = "governance-snapshot-comparison"
COMPARISON_VERSION = "1"
DIRECTION = "baseline_to_candidate"


def _artifact_metadata(snapshot: GovernanceSnapshot) -> dict[str, Any]:
    return {
        "content_identity": snapshot.content_identity().to_dict(),
        "database_name": snapshot.database_name,
        "scanner": snapshot.scanner,
        "scanner_contract_version": snapshot.scanner_contract_version,
        "source_name": snapshot.source_name,
        "system_type": snapshot.system_type,
    }


def _validate_pairwise_scanner(
    baseline: GovernanceSnapshot,
    candidate: GovernanceSnapshot,
) -> None:
    errors: list[DiagnosticError] = []
    if baseline.system_type != candidate.system_type:
        errors.append(
            DiagnosticError(
                code=CODE_SYSTEM_TYPE_MISMATCH,
                path="/system_type",
                message=(
                    f"baseline system_type {baseline.system_type!r} != "
                    f"candidate {candidate.system_type!r}"
                ),
            )
        )
    if baseline.scanner != candidate.scanner:
        errors.append(
            DiagnosticError(
                code=CODE_SCANNER_MISMATCH,
                path="/scanner",
                message=(
                    f"baseline scanner {baseline.scanner!r} != candidate {candidate.scanner!r}"
                ),
            )
        )
    if errors:
        raise ComparisonError(errors)


def build_comparison_result(
    baseline: GovernanceSnapshot,
    candidate: GovernanceSnapshot,
    *,
    ack: RootAlignmentAck | None = None,
) -> dict[str, Any]:
    """Build a complete comparison result dict including content_identity."""
    alignment_ack = ack if ack is not None else RootAlignmentAck()

    projected_baseline = project_snapshot(baseline, side="baseline")
    projected_candidate = project_snapshot(candidate, side="candidate")
    _validate_pairwise_scanner(baseline, candidate)
    alignment = resolve_root_alignment(baseline, candidate, alignment_ack)

    diff = compare_projected(projected_baseline, projected_candidate)
    summary = diff["summary"]
    status = (
        "identical"
        if summary["added"] == 0 and summary["removed"] == 0 and summary["changed"] == 0
        else "different"
    )
    if summary["changed"] == 0 and summary["property_changes"] != 0:
        raise ValueError("inconsistent summary: changed=0 but property_changes!=0")

    without_identity: dict[str, Any] = {
        "baseline": _artifact_metadata(baseline),
        "candidate": _artifact_metadata(candidate),
        "comparison_schema": COMPARISON_SCHEMA,
        "comparison_version": COMPARISON_VERSION,
        "direction": DIRECTION,
        "object_changes": diff["object_changes"],
        "root_alignment": alignment.to_dict(),
        "status": status,
        "summary": summary,
        "writes_performed": 0,
    }
    identity = snapshot_comparison_identity(without_identity)
    return {**without_identity, "content_identity": identity.to_dict()}


def assert_inverse(left: dict[str, Any], right: dict[str, Any]) -> None:
    """Assert that ``right`` is the directional inverse of ``left``."""
    assert left["baseline"] == right["candidate"]
    assert left["candidate"] == right["baseline"]
    assert left["summary"]["added"] == right["summary"]["removed"]
    assert left["summary"]["removed"] == right["summary"]["added"]
    assert left["summary"]["changed"] == right["summary"]["changed"]
    assert left["summary"]["unchanged"] == right["summary"]["unchanged"]
    assert left["summary"]["property_changes"] == right["summary"]["property_changes"]

    def _swap_alignment(block: dict[str, Any] | None) -> dict[str, Any] | None:
        if block is None:
            return None
        return {"baseline": block["candidate"], "candidate": block["baseline"]}

    assert left["root_alignment"]["source"] == _swap_alignment(right["root_alignment"]["source"])
    assert left["root_alignment"]["database"] == _swap_alignment(
        right["root_alignment"]["database"]
    )

    left_by_key = {
        (item["change"], canonical_identity_key(item["object_identity"])): item
        for item in left["object_changes"]
    }
    right_by_key = {
        (item["change"], canonical_identity_key(item["object_identity"])): item
        for item in right["object_changes"]
    }

    for (change, key), item in left_by_key.items():
        if change == "added":
            inverse = right_by_key[("removed", key)]
            assert inverse["property_changes"] == []
        elif change == "removed":
            inverse = right_by_key[("added", key)]
            assert inverse["property_changes"] == []
        else:
            inverse = right_by_key[("changed", key)]
            assert len(item["property_changes"]) == len(inverse["property_changes"])
            left_props = {p["property"]: p for p in item["property_changes"]}
            right_props = {p["property"]: p for p in inverse["property_changes"]}
            assert set(left_props) == set(right_props)
            for prop, left_change in left_props.items():
                right_change = right_props[prop]
                assert left_change["baseline"] == right_change["candidate"]
                assert left_change["candidate"] == right_change["baseline"]


def canonical_identity_key(identity: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    return (str(identity["kind"]), tuple(str(item) for item in identity["path"]))


# Re-export for callers that need the type
__all__ = [
    "COMPARISON_SCHEMA",
    "COMPARISON_VERSION",
    "DIRECTION",
    "RootAlignmentAck",
    "RootAlignmentResult",
    "assert_inverse",
    "build_comparison_result",
    "canonical_identity_key",
]
