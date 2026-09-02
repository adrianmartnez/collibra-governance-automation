"""Review operation tests for the official GitHub Action orchestration (issue #79)."""

from __future__ import annotations

import argparse
import io
import json
import math
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from conftest_drift import (
    write_different_description_comparison,
    write_full_description_policy,
    write_identical_comparison,
    write_policy,
)
from conftest_history import (
    NS,
    build_observation_set,
    write_authority_yaml,
    write_observations,
    write_sample_snapshot,
)
from governance.domain.authority import (
    AuthorityRuleKey,
    AuthoritySelector,
    AuthorityTarget,
    NormalizedAuthorityPolicySet,
)
from governance.domain.conflicts import (
    PropertyConflictReport,
    PropertyConflictResult,
    analyze_property_conflicts,
)
from governance.domain.graph import GraphNodeIdentity, ProvenanceRecord
from governance.domain.observations import PropertyObservation, PropertyObservationSet, PropertyPath
from governance.drift import build_drift_result, load_drift_policy
from governance.github_ci.comment import build_comment_body
from governance.github_ci.report import (
    MAX_ANNOTATIONS,
    MAX_REVIEW_CONFLICT_ITEMS,
    MAX_REVIEW_DRIFT_ITEMS,
    build_review_annotations,
    render_review_report,
)
from governance.github_ci.result import ANNOTATIONS_NAME, REPORT_NAME
from governance.github_ci.review import (
    COMPARISON_RESULT_NAME,
    DRIFT_RESULT_NAME,
    REVIEW_RESULT_NAME,
    ReviewResultError,
    build_conflicts_block,
    build_drift_block,
    build_review_result,
    canonical_review_json,
    desired_review_exit_code,
    load_review_result_artifact,
    validate_review_result,
    write_review_result,
)
from governance.github_ci.runner import run_orchestration, scrubbed_env
from governance.reconciliation.safety import assess_reconciliation
from governance.reconciliation.targets import target_field_for_path

CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"
SENTINEL = "TOP_SECRET_DO_NOT_RENDER_79"


def _read_github_output(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _review_args(**overrides: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "config": "",
        "profile": "",
        "operation": "review",
        "output_format": "human",
        "fail_on_policy_error": "true",
        "fail_on_review_blocked": "true",
        "output_directory": ".governance",
        "plan_path": ".governance/governance.gplan",
        "pr_comment": "false",
        "impact_namespace": "",
        "impact_changes": "",
        "impact_odcs": "",
        "impact_dbt_manifest": "",
        "impact_openlineage": "",
        "dbt_default_database": "",
        "review_observations": "",
        "review_comparison": "",
        "review_baseline_snapshot": "",
        "review_candidate_snapshot": "",
        "review_drift_policy": "",
        "review_align_source_roots": "false",
        "review_align_database_roots": "false",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _run_env(workspace: Path, tmp_path: Path) -> dict[str, str]:
    return {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(tmp_path / "github_output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step_summary.md"),
    }


def _prov(
    provider: str,
    ref: str,
    *,
    version: str | None = "1.0",
    mode: str = "observed",
) -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type=provider,
        source_ref=ref,
        source_version=version,
        observation_mode=mode,
    )


def _conflicting_observations(
    *,
    property_pointer: str = "/description",
    logical_id: str = "orders",
    kind: str = "table",
) -> PropertyObservationSet:
    identity = GraphNodeIdentity(NS, kind, logical_id)
    path = PropertyPath.parse(property_pointer)
    return PropertyObservationSet.from_observations(
        [
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="from-odcs",
                provenance=(_prov("odcs", "customer-contract"),),
            ),
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="from-dbt",
                provenance=(_prov("dbt", "manifest"),),
            ),
        ]
    )


def _clear_conflicts_block() -> dict[str, Any]:
    observations = build_observation_set()
    authority = NormalizedAuthorityPolicySet()
    report = analyze_property_conflicts(observations, authority)
    assessments = [assess_reconciliation(result) for result in report.results]
    return build_conflicts_block(
        report=report,
        assessments=assessments,
        observations_content_identity=observations.content_identity().to_dict(),
        authority_content_identity=authority.content_identity().to_dict(),
    )


def _identical_drift_block(tmp_path: Path) -> dict[str, Any]:
    comparison = write_identical_comparison(tmp_path / "cmp.json")
    drift_result = build_drift_result(comparison, None)
    return build_drift_block(comparison=comparison, drift_result=drift_result)


def _write_config_with_authority(workspace: Path, authority_rel: str = "auth.yaml") -> str:
    mapping = CONFIG_FIXTURES / "mapping.json"
    (workspace / "mapping.json").write_text(mapping.read_text(encoding="utf-8"), encoding="utf-8")
    (workspace / "governance.yaml").write_text(
        "\n".join(
            [
                'schema_version: "1"',
                "sources:",
                "  - id: primary",
                "    provider: postgresql",
                "    config:",
                "      source_name: governance-demo",
                "      connection:",
                "        database_url_env: DATABASE_URL",
                "targets:",
                "  - id: collibra",
                "    provider: collibra",
                "    config:",
                "      mode_env: COLLIBRA_MODE",
                "      mapping:",
                "        path: mapping.json",
                "      auth:",
                "        base_url_env: COLLIBRA_BASE_URL",
                "authority:",
                "  files:",
                f"    - {authority_rel}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return "governance.yaml"


def _phase_a(
    tmp_path: Path,
    *,
    args: argparse.Namespace,
    workspace: Path | None = None,
) -> tuple[int, dict[str, str], Path]:
    ws = workspace if workspace is not None else tmp_path / "ws"
    if not ws.exists():
        ws.mkdir()
    out = tmp_path / "github_output"
    env = {
        "GITHUB_WORKSPACE": str(ws),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step_summary.md"),
    }
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(args, stdout=io.StringIO())
    return code, _read_github_output(out), ws


# --- Schema / identity -------------------------------------------------------


def test_review_result_conflict_only_validates() -> None:
    result = build_review_result(conflicts=_clear_conflicts_block())
    assert result["conflicts"]["status"] == "clear"
    assert result["drift"]["status"] == "not_run"
    assert result["status"] == "passed"
    validated = validate_review_result(result)
    assert validated["content_identity"]["digest"] == result["content_identity"]["digest"]


def test_review_result_drift_only_validates(tmp_path: Path) -> None:
    result = build_review_result(drift=_identical_drift_block(tmp_path))
    assert result["conflicts"]["status"] == "not_run"
    assert result["drift"]["status"] == "no_difference"
    assert result["status"] == "passed"
    validate_review_result(result)


def test_review_result_combined_validates(tmp_path: Path) -> None:
    result = build_review_result(
        conflicts=_clear_conflicts_block(),
        drift=_identical_drift_block(tmp_path),
    )
    assert result["conflicts"]["status"] == "clear"
    assert result["drift"]["status"] == "no_difference"
    assert result["status"] == "passed"
    validate_review_result(result)


def test_review_result_rejects_additional_properties() -> None:
    result = build_review_result()
    result["extra_field"] = True
    with pytest.raises(ReviewResultError):
        validate_review_result(result)


def test_review_result_rejects_bad_enum() -> None:
    result = build_review_result()
    result["status"] = "weird"
    with pytest.raises(ReviewResultError):
        validate_review_result(result)


def test_review_result_rejects_negative_counts() -> None:
    result = build_review_result(conflicts=_clear_conflicts_block())
    result["conflicts"]["summary"]["properties_analyzed"] = -1
    with pytest.raises(ReviewResultError):
        validate_review_result(result)


def test_review_result_rejects_writes_performed_nonzero() -> None:
    result = build_review_result()
    result["writes_performed"] = 1
    with pytest.raises(ReviewResultError):
        validate_review_result(result)


def test_corrupted_content_identity_rejected(tmp_path: Path) -> None:
    result = build_review_result()
    result["content_identity"] = {
        **result["content_identity"],
        "digest": "0" * 64,
    }
    path = tmp_path / "review-result.json"
    path.write_text(canonical_review_json(result), encoding="utf-8")
    with pytest.raises(ReviewResultError) as exc_info:
        load_review_result_artifact(path)
    assert any(item.code == "integrity_mismatch" for item in exc_info.value.errors)


def test_equivalent_semantic_byte_identical_canonical() -> None:
    left = build_review_result(conflicts=_clear_conflicts_block())
    right = build_review_result(conflicts=_clear_conflicts_block())
    assert canonical_review_json(left) == canonical_review_json(right)


def test_empty_authority_identity_non_null_deterministic() -> None:
    first = NormalizedAuthorityPolicySet().content_identity()
    second = NormalizedAuthorityPolicySet().content_identity()
    assert first.to_dict() == second.to_dict()
    assert first.digest
    assert first.algorithm == "sha256"


def test_load_review_duplicate_keys_bounded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "dup.json"
    path.write_text('{"a":1,"a":2}\n', encoding="utf-8")
    with pytest.raises(ReviewResultError) as exc_info:
        load_review_result_artifact(path)
    assert exc_info.value.errors[0].code == "parse_error"
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_load_review_nan_bounded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{"value": NaN}\n', encoding="utf-8")
    with pytest.raises(ReviewResultError) as exc_info:
        load_review_result_artifact(path)
    assert exc_info.value.errors[0].code == "parse_error"
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err


def test_load_review_iter_errors_recursion_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "ok.json"
    write_review_result(build_review_result(), path)

    def _boom(self: Any, instance: Any) -> Any:
        raise RecursionError("too deep")

    monkeypatch.setattr(
        "governance.github_ci.review.Draft202012Validator.iter_errors",
        _boom,
    )
    with pytest.raises(ReviewResultError) as exc_info:
        load_review_result_artifact(path)
    assert exc_info.value.errors[0].code == "invalid_artifact"
    assert "deeply nested" in exc_info.value.errors[0].message
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err


# --- Phase A ------------------------------------------------------------------


def test_phase_a_review_accepted_with_observations(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", build_observation_set())
    code, values, ws = _phase_a(
        tmp_path,
        workspace=workspace,
        args=_review_args(review_observations="obs.json"),
    )
    assert code == 0
    assert values["phase-a-failed"] == "false"
    assert values["review-status"] == "passed"
    assert values["conflict-status"] == "clear"
    assert (ws / values["review-result-path"]).is_file()


def test_phase_a_no_lane_fails(tmp_path: Path) -> None:
    code, values, ws = _phase_a(tmp_path, args=_review_args())
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert values["desired-exit-code"] == "1"
    assert values["review-status"] == "failed"
    assert not (ws / ".governance").exists()
    assert not (ws / ".governance" / REVIEW_RESULT_NAME).exists()


def test_phase_a_baseline_only_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_sample_snapshot(workspace / "base.json")
    code, values, ws = _phase_a(
        tmp_path,
        workspace=workspace,
        args=_review_args(review_baseline_snapshot="base.json"),
    )
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance").exists()


def test_phase_a_candidate_only_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_sample_snapshot(workspace / "cand.json")
    code, values, ws = _phase_a(
        tmp_path,
        workspace=workspace,
        args=_review_args(review_candidate_snapshot="cand.json"),
    )
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance").exists()


def test_phase_a_comparison_plus_baseline_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_identical_comparison(workspace / "cmp.json")
    write_sample_snapshot(workspace / "base.json")
    write_sample_snapshot(workspace / "cand.json")
    code, values, ws = _phase_a(
        tmp_path,
        workspace=workspace,
        args=_review_args(
            review_comparison="cmp.json",
            review_baseline_snapshot="base.json",
            review_candidate_snapshot="cand.json",
        ),
    )
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance").exists()


def test_phase_a_align_plus_comparison_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_identical_comparison(workspace / "cmp.json")
    code, values, ws = _phase_a(
        tmp_path,
        workspace=workspace,
        args=_review_args(
            review_comparison="cmp.json",
            review_align_source_roots="true",
        ),
    )
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance").exists()


def test_phase_a_drift_policy_without_drift_lane_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", build_observation_set())
    write_full_description_policy(workspace / "policy.yaml")
    code, values, ws = _phase_a(
        tmp_path,
        workspace=workspace,
        args=_review_args(
            review_observations="obs.json",
            review_drift_policy="policy.yaml",
        ),
    )
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance").exists()


def test_phase_a_config_without_observations_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_identical_comparison(workspace / "cmp.json")
    (workspace / "governance.yaml").write_text('schema_version: "1"\n', encoding="utf-8")
    code, values, ws = _phase_a(
        tmp_path,
        workspace=workspace,
        args=_review_args(config="governance.yaml", review_comparison="cmp.json"),
    )
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance").exists()


def test_phase_a_profile_without_config_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", build_observation_set())
    code, values, ws = _phase_a(
        tmp_path,
        workspace=workspace,
        args=_review_args(profile="prod", review_observations="obs.json"),
    )
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance").exists()


def test_phase_a_absolute_path_fails(tmp_path: Path) -> None:
    code, values, ws = _phase_a(
        tmp_path,
        args=_review_args(review_observations="/tmp/obs.json"),
    )
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance").exists()


def test_phase_a_output_collision_with_review_result_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    code, values, ws = _phase_a(
        tmp_path,
        workspace=workspace,
        args=_review_args(review_observations=".governance/review-result.json"),
    )
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance" / REVIEW_RESULT_NAME).exists()


def test_phase_a_creates_no_review_result_json(tmp_path: Path) -> None:
    code, values, ws = _phase_a(tmp_path, args=_review_args())
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert values.get("review-result-path", "") == ""
    assert list(ws.rglob(REVIEW_RESULT_NAME)) == []


# --- Conflict semantics via run_orchestration ---------------------------------


def test_conflict_single_observation_empty_authority(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", build_observation_set())
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_observations="obs.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["phase-a-failed"] == "false"
    assert values["status"] == "passed"
    assert values["review-status"] == "passed"
    assert values["conflict-status"] == "clear"
    payload = load_review_result_artifact(workspace / values["review-result-path"])
    empty_identity = NormalizedAuthorityPolicySet().content_identity().to_dict()
    assert payload["conflicts"]["authority_content_identity"] == empty_identity
    assert payload["conflicts"]["authority_content_identity"] is not None


def test_conflict_mapped_description_blocked_without_authority(tmp_path: Path) -> None:
    assert target_field_for_path(PropertyPath.parse("/description")) == "description"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", _conflicting_observations())
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_observations="obs.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "blocked"
    assert values["review-status"] == "blocked"
    assert values["conflict-status"] == "blocked"
    assert values["desired-exit-code"] == "3"
    assert int(values["unresolved-conflict-count"]) >= 1
    assert int(values["reconciliation-blocked-count"]) >= 1


def test_conflict_non_mapped_property_findings_passed(tmp_path: Path) -> None:
    pointer = "/attributes/foo"
    assert target_field_for_path(PropertyPath.parse(pointer)) is None
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(
        workspace / "obs.json",
        _conflicting_observations(property_pointer=pointer),
    )
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_observations="obs.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["conflict-status"] == "findings"
    assert values["status"] == "passed"
    assert values["review-status"] == "passed"
    assert values["desired-exit-code"] == "0"
    assert int(values["unresolved-conflict-count"]) >= 1
    assert int(values["reconciliation-blocked-count"]) == 0


def test_conflict_resolved_by_authority(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_authority_yaml(workspace / "auth.yaml")
    config = _write_config_with_authority(workspace, "auth.yaml")
    write_observations(workspace / "obs.json", _conflicting_observations())
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(config=config, review_observations="obs.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "passed"
    assert values["review-status"] == "passed"
    assert values["conflict-status"] == "clear"
    assert int(values["resolved-authority-count"]) > 0
    assert values["desired-exit-code"] == "0"


def test_fail_on_review_blocked_false_with_blocked(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", _conflicting_observations())
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_observations="obs.json",
                fail_on_review_blocked="false",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "blocked"
    assert values["review-status"] == "blocked"
    assert values["desired-exit-code"] == "0"


# --- Drift semantics ----------------------------------------------------------


def test_drift_identical_no_policy_passed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_identical_comparison(workspace / "cmp.json")
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_comparison="cmp.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["drift-status"] == "no_difference"
    assert values["status"] == "passed"
    assert values["review-status"] == "passed"
    assert values["review-result-version"] == "1"
    assert values["desired-exit-code"] == "0"


def test_drift_different_with_policy_expected(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_different_description_comparison(workspace / "cmp.json")
    write_full_description_policy(workspace / "policy.yaml")
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_comparison="cmp.json",
                review_drift_policy="policy.yaml",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["drift-status"] == "expected_difference"
    assert values["status"] == "passed"
    assert values["review-status"] == "passed"
    assert int(values["expected-difference-count"]) >= 1
    assert values["desired-exit-code"] == "0"


def test_drift_different_without_policy_configuration_failed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_different_description_comparison(workspace / "cmp.json")
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_comparison="cmp.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "failed"
    assert values["review-status"] == "failed"
    assert values["review-result-version"] == ""
    assert values["desired-exit-code"] == "1"
    report = (workspace / values["report-path"]).read_text(encoding="utf-8")
    assert "configuration_failed" in report


def test_drift_unexpected_blocked_exit_3(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_different_description_comparison(workspace / "cmp.json")
    write_policy(
        workspace / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
""",
    )
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_comparison="cmp.json",
                review_drift_policy="policy.yaml",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["drift-status"] == "unexpected_drift"
    assert values["status"] == "blocked"
    assert values["review-status"] == "blocked"
    assert values["desired-exit-code"] == "3"
    assert int(values["unexpected-drift-count"]) >= 1


def test_drift_unexpected_fail_on_false_exit_0(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_different_description_comparison(workspace / "cmp.json")
    write_policy(
        workspace / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
""",
    )
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_comparison="cmp.json",
                review_drift_policy="policy.yaml",
                fail_on_review_blocked="false",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "blocked"
    assert values["desired-exit-code"] == "0"


# --- Reporting / secrets ------------------------------------------------------


def test_sentinel_secret_not_rendered_in_human_surfaces(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(
        workspace / "obs.json",
        build_observation_set(value=SENTINEL, source_ref=SENTINEL),
    )
    out = tmp_path / "github_output"
    summary = tmp_path / "step_summary.md"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    buf = io.StringIO()
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_observations="obs.json", output_format="human"),
            stdout=buf,
        )
    assert code == 0
    values = _read_github_output(out)
    review_text = (workspace / values["review-result-path"]).read_text(encoding="utf-8")
    assert SENTINEL in review_text

    report = (workspace / values["report-path"]).read_text(encoding="utf-8")
    annotations = (workspace / values["annotations-path"]).read_text(encoding="utf-8")
    output_blob = "\n".join(f"{k}={v}" for k, v in values.items())
    step = summary.read_text(encoding="utf-8") if summary.is_file() else ""
    captured = capsys.readouterr()
    surfaces = [report, annotations, output_blob, step, buf.getvalue(), captured.out, captured.err]
    for surface in surfaces:
        assert SENTINEL not in surface


# --- Token isolation ----------------------------------------------------------


def test_scrubbed_env_removes_github_token() -> None:
    scrubbed = scrubbed_env(
        {
            "PATH": "/usr/bin",
            "GITHUB_TOKEN": "leak-token",
            "GH_TOKEN": "leak-token",
            "INPUT_GITHUB_TOKEN": "leak-token",
            "GOVERNANCE_COMMENT_TOKEN": "leak-token",
            "GITHUB_WORKSPACE": "/tmp/ws",
        }
    )
    assert "GITHUB_TOKEN" not in scrubbed
    assert "GH_TOKEN" not in scrubbed
    assert "INPUT_GITHUB_TOKEN" not in scrubbed
    assert "GOVERNANCE_COMMENT_TOKEN" not in scrubbed
    assert scrubbed["PATH"] == "/usr/bin"
    assert scrubbed["GITHUB_WORKSPACE"] == "/tmp/ws"


# --- Zero remote I/O ----------------------------------------------------------


def test_conflict_only_review_zero_remote_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def boom(name: str):
        def _inner(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            raise AssertionError(f"{name} must not be called during review")

        return _inner

    monkeypatch.setattr(
        "governance.integrations.collibra.build_collibra_adapter",
        boom("build_collibra_adapter"),
    )
    monkeypatch.setattr(
        "governance.cli.build_collibra_adapter",
        boom("cli.build_collibra_adapter"),
    )
    httpx = pytest.importorskip("httpx")
    monkeypatch.setattr(httpx, "Client", boom("httpx.Client"))
    monkeypatch.setattr(httpx, "request", boom("httpx.request"))
    try:
        import requests
    except ImportError:
        requests = None  # type: ignore[assignment]
    if requests is not None:
        monkeypatch.setattr(requests, "request", boom("requests.request"))
        monkeypatch.setattr(requests, "get", boom("requests.get"))
        monkeypatch.setattr(requests, "post", boom("requests.post"))

    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", build_observation_set())
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_observations="obs.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["review-status"] == "passed"
    assert calls == []


# --- Backward smoke -----------------------------------------------------------


def test_desired_review_exit_code_smoke() -> None:
    assert desired_review_exit_code(status="passed", fail_on_review_blocked=True) == 0
    assert desired_review_exit_code(status="blocked", fail_on_review_blocked=True) == 3
    assert desired_review_exit_code(status="blocked", fail_on_review_blocked=False) == 0
    assert desired_review_exit_code(status="failed", fail_on_review_blocked=True) == 1


def _rehash_review(payload: dict) -> dict:
    from governance.identity.hashing import ci_review_result_identity

    without = {k: v for k, v in payload.items() if k != "content_identity"}
    payload = dict(payload)
    payload["content_identity"] = ci_review_result_identity(without).to_dict()
    return payload


def _blocked_conflicts_block() -> dict[str, Any]:
    observations = _conflicting_observations()
    authority = NormalizedAuthorityPolicySet()
    report = analyze_property_conflicts(observations, authority)
    assessments = [assess_reconciliation(result) for result in report.results]
    return build_conflicts_block(
        report=report,
        assessments=assessments,
        observations_content_identity=observations.content_identity().to_dict(),
        authority_content_identity=authority.content_identity().to_dict(),
    )


def _expected_drift_block(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    comparison = write_different_description_comparison(tmp_path / "cmp-exp.json")
    write_full_description_policy(tmp_path / "pol-exp.yaml")
    policy = load_drift_policy(tmp_path / "pol-exp.yaml")
    drift_result = build_drift_result(comparison, policy)
    return build_drift_block(comparison=comparison, drift_result=drift_result), drift_result


def _unexpected_drift_block(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    comparison = write_different_description_comparison(tmp_path / "cmp-unexp.json")
    write_policy(
        tmp_path / "pol-unexp.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
""",
    )
    policy = load_drift_policy(tmp_path / "pol-unexp.yaml")
    drift_result = build_drift_result(comparison, policy)
    return build_drift_block(comparison=comparison, drift_result=drift_result), drift_result


# --- V2 Blocker 1 — AuthorityRuleKey schema ---------------------------------


def test_provider_only_authority_resolves_and_validates(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_authority_yaml(workspace / "auth.yaml", source_ref=None)
    config = _write_config_with_authority(workspace, "auth.yaml")
    write_observations(workspace / "obs.json", _conflicting_observations())
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(config=config, review_observations="obs.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["review-status"] == "passed"
    payload = load_review_result_artifact(workspace / values["review-result-path"])
    resolved = [
        item
        for item in payload["conflicts"]["conflict_report"]["results"]
        if item["state"] == "RESOLVED_BY_AUTHORITY"
    ]
    assert resolved
    winning = resolved[0]["winning_rule_key"]
    assert "source_ref" not in winning["authority"]
    assert winning["authority"]["provider_type"] == "odcs"
    validate_review_result(payload)


def test_namespace_specific_authority_resolves_and_validates(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_authority_yaml(workspace / "auth.yaml", namespace=NS)
    config = _write_config_with_authority(workspace, "auth.yaml")
    write_observations(workspace / "obs.json", _conflicting_observations())
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(config=config, review_observations="obs.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["review-status"] == "passed"
    payload = load_review_result_artifact(workspace / values["review-result-path"])
    resolved = [
        item
        for item in payload["conflicts"]["conflict_report"]["results"]
        if item["state"] == "RESOLVED_BY_AUTHORITY"
    ]
    assert resolved
    assert resolved[0]["winning_rule_key"]["select"]["namespace"] == NS
    validate_review_result(payload)


def test_provider_only_and_namespace_authority_combined_validates(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_authority_yaml(workspace / "auth.yaml", source_ref=None, namespace=NS)
    config = _write_config_with_authority(workspace, "auth.yaml")
    write_observations(workspace / "obs.json", _conflicting_observations())
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(config=config, review_observations="obs.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["review-status"] == "passed"
    payload = load_review_result_artifact(workspace / values["review-result-path"])
    resolved = [
        item
        for item in payload["conflicts"]["conflict_report"]["results"]
        if item["state"] == "RESOLVED_BY_AUTHORITY"
    ]
    assert resolved
    winning = resolved[0]["winning_rule_key"]
    assert "source_ref" not in winning["authority"]
    assert winning["select"]["namespace"] == NS
    validate_review_result(payload)


def test_winning_rule_key_rejects_unknown_extra_field(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_authority_yaml(workspace / "auth.yaml", source_ref=None)
    config = _write_config_with_authority(workspace, "auth.yaml")
    write_observations(workspace / "obs.json", _conflicting_observations())
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(config=config, review_observations="obs.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    payload = load_review_result_artifact(workspace / values["review-result-path"])
    resolved = [
        item
        for item in payload["conflicts"]["conflict_report"]["results"]
        if item["state"] == "RESOLVED_BY_AUTHORITY"
    ]
    assert resolved
    forged = dict(payload)
    forged["conflicts"] = dict(payload["conflicts"])
    forged["conflicts"]["conflict_report"] = dict(payload["conflicts"]["conflict_report"])
    results = [dict(item) for item in forged["conflicts"]["conflict_report"]["results"]]
    idx = next(i for i, item in enumerate(results) if item["state"] == "RESOLVED_BY_AUTHORITY")
    results[idx] = dict(results[idx])
    results[idx]["winning_rule_key"] = dict(results[idx]["winning_rule_key"])
    results[idx]["winning_rule_key"]["extra_field"] = True
    forged["conflicts"]["conflict_report"]["results"] = results
    forged = _rehash_review(forged)
    with pytest.raises(ReviewResultError):
        validate_review_result(forged)


# --- V2 Blocker 2 — Active lane invariants ----------------------------------


def test_forged_blocked_conflicts_null_identities_rejected() -> None:
    result = build_review_result(conflicts=_blocked_conflicts_block())
    forged = dict(result)
    forged["conflicts"] = dict(result["conflicts"])
    forged["conflicts"]["observations_content_identity"] = None
    forged["conflicts"]["authority_content_identity"] = None
    forged["conflicts"]["conflict_report_content_identity"] = None
    forged = _rehash_review(forged)
    with pytest.raises(ReviewResultError):
        validate_review_result(forged)


def test_forged_conflicts_false_summary_counts_rejected() -> None:
    result = build_review_result(conflicts=_clear_conflicts_block())
    forged = dict(result)
    forged["conflicts"] = dict(result["conflicts"])
    forged["conflicts"]["summary"] = dict(result["conflicts"]["summary"])
    forged["conflicts"]["summary"]["unresolved_conflict"] = 99
    forged = _rehash_review(forged)
    with pytest.raises(ReviewResultError):
        validate_review_result(forged)


def test_forged_conflict_report_identity_mismatch_rejected() -> None:
    result = build_review_result(conflicts=_clear_conflicts_block())
    forged = dict(result)
    forged["conflicts"] = dict(result["conflicts"])
    identity = dict(result["conflicts"]["conflict_report_content_identity"])
    identity["digest"] = "0" * 64
    forged["conflicts"]["conflict_report_content_identity"] = identity
    forged = _rehash_review(forged)
    with pytest.raises(ReviewResultError):
        validate_review_result(forged)


def test_forged_assessment_object_misaligned_rejected() -> None:
    result = build_review_result(conflicts=_clear_conflicts_block())
    forged = dict(result)
    forged["conflicts"] = dict(result["conflicts"])
    assessments = [dict(item) for item in result["conflicts"]["reconciliation_assessments"]]
    assessments[0] = dict(assessments[0])
    assessments[0]["object"] = dict(assessments[0]["object"])
    assessments[0]["object"]["logical_id"] = "misaligned"
    forged["conflicts"]["reconciliation_assessments"] = assessments
    forged = _rehash_review(forged)
    with pytest.raises(ReviewResultError):
        validate_review_result(forged)


def test_forged_conflicts_status_contradictory_rejected() -> None:
    result = build_review_result(conflicts=_blocked_conflicts_block())
    forged = dict(result)
    forged["conflicts"] = dict(result["conflicts"])
    forged["conflicts"]["status"] = "clear"
    forged["status"] = "passed"
    forged = _rehash_review(forged)
    with pytest.raises(ReviewResultError):
        validate_review_result(forged)


def test_forged_active_drift_null_identities_rejected(tmp_path: Path) -> None:
    result = build_review_result(drift=_identical_drift_block(tmp_path))
    forged = dict(result)
    forged["drift"] = dict(result["drift"])
    forged["drift"]["baseline_snapshot_identity"] = None
    forged["drift"]["candidate_snapshot_identity"] = None
    forged["drift"]["comparison_content_identity"] = None
    forged["drift"]["drift_result_content_identity"] = None
    forged = _rehash_review(forged)
    with pytest.raises(ReviewResultError):
        validate_review_result(forged)


def test_forged_expected_difference_null_policy_identity_rejected(tmp_path: Path) -> None:
    drift, _ = _expected_drift_block(tmp_path)
    result = build_review_result(drift=drift)
    forged = dict(result)
    forged["drift"] = dict(result["drift"])
    forged["drift"]["drift_policy_identity"] = None
    forged = _rehash_review(forged)
    with pytest.raises(ReviewResultError):
        validate_review_result(forged)


def test_forged_unexpected_drift_zero_count_rejected(tmp_path: Path) -> None:
    drift, _ = _unexpected_drift_block(tmp_path)
    result = build_review_result(drift=drift)
    forged = dict(result)
    forged["drift"] = dict(result["drift"])
    forged["drift"]["summary"] = dict(result["drift"]["summary"])
    forged["drift"]["summary"]["unexpected_drift"] = 0
    forged = _rehash_review(forged)
    with pytest.raises(ReviewResultError):
        validate_review_result(forged)


def test_forged_no_difference_nonzero_count_rejected(tmp_path: Path) -> None:
    result = build_review_result(drift=_identical_drift_block(tmp_path))
    forged = dict(result)
    forged["drift"] = dict(result["drift"])
    forged["drift"]["summary"] = dict(result["drift"]["summary"])
    forged["drift"]["summary"]["affected_objects"] = 1
    forged = _rehash_review(forged)
    with pytest.raises(ReviewResultError):
        validate_review_result(forged)


def test_forged_root_status_contradictory_rejected() -> None:
    result = build_review_result(conflicts=_blocked_conflicts_block())
    assert result["status"] == "blocked"
    forged = dict(result)
    forged["status"] = "passed"
    forged = _rehash_review(forged)
    with pytest.raises(ReviewResultError):
        validate_review_result(forged)


def test_write_review_result_rejects_forged_and_does_not_write(tmp_path: Path) -> None:
    result = build_review_result(conflicts=_blocked_conflicts_block())
    forged = dict(result)
    forged["conflicts"] = dict(result["conflicts"])
    forged["conflicts"]["observations_content_identity"] = None
    forged["conflicts"]["authority_content_identity"] = None
    forged["conflicts"]["conflict_report_content_identity"] = None
    forged = _rehash_review(forged)
    path = tmp_path / "out" / REVIEW_RESULT_NAME
    with pytest.raises(ReviewResultError):
        write_review_result(forged, path)
    assert not path.exists()


# --- V2 Blocker 3 — parent-aware report -------------------------------------


def test_report_parent_disambiguates_same_child_logical_id() -> None:
    parent_a = GraphNodeIdentity(NS, "table", "orders")
    parent_b = GraphNodeIdentity(NS, "table", "customers")
    col_a = GraphNodeIdentity(NS, "column", "id", parent=parent_a)
    col_b = GraphNodeIdentity(NS, "column", "id", parent=parent_b)
    path = PropertyPath.parse("/attributes/data_type")
    unresolved = PropertyConflictResult(
        object_identity=col_a,
        property_path=path,
        state="UNRESOLVED_CONFLICT",
        reason="NO_AUTHORITY_RULE",
        value_groups=(
            PropertyObservation(
                object_identity=col_a,
                property_path=path,
                value="int",
                provenance=(_prov("odcs", "a"),),
            ),
            PropertyObservation(
                object_identity=col_a,
                property_path=path,
                value="string",
                provenance=(_prov("dbt", "b"),),
            ),
        ),
    )
    winning = AuthorityRuleKey(
        selector=AuthoritySelector(kind="column", property_path=path, namespace=NS),
        authority=AuthorityTarget(provider_type="odcs"),
    )
    resolved = PropertyConflictResult(
        object_identity=col_b,
        property_path=path,
        state="RESOLVED_BY_AUTHORITY",
        reason="RESOLVED_BY_AUTHORITY",
        value_groups=(
            PropertyObservation(
                object_identity=col_b,
                property_path=path,
                value="string",
                provenance=(_prov("odcs", "a"),),
            ),
        ),
        effective_value="string",
        has_effective_value=True,
        winning_rule_key=winning,
    )
    report = PropertyConflictReport(results=(unresolved, resolved))
    assessments = [assess_reconciliation(item) for item in report.results]
    block = build_conflicts_block(
        report=report,
        assessments=assessments,
        observations_content_identity=build_observation_set().content_identity().to_dict(),
        authority_content_identity=NormalizedAuthorityPolicySet().content_identity().to_dict(),
    )
    result = build_review_result(conflicts=block)
    markdown = render_review_report(
        review_status=result["status"],
        review_result=result,
        conflict_status=block["status"],
    )
    assert "blocks_reconciliation=true" in markdown
    assert "blocks_reconciliation=false" in markdown
    assert "logical_id=id" in markdown
    assert "UNRESOLVED_CONFLICT" in markdown
    assert "RESOLVED_BY_AUTHORITY" in markdown


# --- V2 Blocker 4A — Phase A collisions / paths -----------------------------


@pytest.mark.parametrize(
    "artifact_name",
    [
        REVIEW_RESULT_NAME,
        COMPARISON_RESULT_NAME,
        DRIFT_RESULT_NAME,
        REPORT_NAME,
        ANNOTATIONS_NAME,
    ],
)
def test_phase_a_output_collision_parametrized(tmp_path: Path, artifact_name: str) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    if artifact_name in {COMPARISON_RESULT_NAME, DRIFT_RESULT_NAME}:
        args = _review_args(review_comparison=f".governance/{artifact_name}")
    else:
        args = _review_args(review_observations=f".governance/{artifact_name}")
    code, values, ws = _phase_a(tmp_path, workspace=workspace, args=args)
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance" / REVIEW_RESULT_NAME).exists()


def test_phase_a_nul_cr_lf_paths_fail(tmp_path: Path) -> None:
    for bad in ("obs\x00.json", "obs\r.json", "obs\n.json"):
        code, values, ws = _phase_a(
            tmp_path,
            args=_review_args(review_observations=bad),
        )
        assert code == 0
        assert values["phase-a-failed"] == "true"
        assert not (ws / ".governance").exists()


def test_phase_a_dotdot_escape_fails(tmp_path: Path) -> None:
    code, values, ws = _phase_a(
        tmp_path,
        args=_review_args(review_observations="../outside/obs.json"),
    )
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance").exists()


def test_phase_a_no_mkdir_on_failure(tmp_path: Path) -> None:
    code, values, ws = _phase_a(tmp_path, args=_review_args())
    assert code == 0
    assert values["phase-a-failed"] == "true"
    assert not (ws / ".governance").exists()


# --- V2 Blocker 4B — Conflict semantics -------------------------------------


def test_conflict_agreement_same_value_observations(tmp_path: Path) -> None:
    identity = GraphNodeIdentity(NS, "table", "orders")
    path = PropertyPath.parse("/description")
    observations = PropertyObservationSet.from_observations(
        [
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="shared",
                provenance=(_prov("odcs", "customer-contract"),),
            ),
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="shared",
                provenance=(_prov("dbt", "manifest"),),
            ),
        ]
    )
    authority = NormalizedAuthorityPolicySet()
    report = analyze_property_conflicts(observations, authority)
    assert report.results[0].state == "AGREEMENT"
    assessments = [assess_reconciliation(item) for item in report.results]
    block = build_conflicts_block(
        report=report,
        assessments=assessments,
        observations_content_identity=observations.content_identity().to_dict(),
        authority_content_identity=authority.content_identity().to_dict(),
    )
    assert block["status"] == "clear"
    assert block["summary"]["agreement"] == 1
    result = build_review_result(conflicts=block)
    validate_review_result(result)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", observations)
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_observations="obs.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["conflict-status"] == "clear"
    assert values["review-status"] == "passed"


def test_conflict_invalid_or_ambiguous_authority_builder() -> None:
    identity = GraphNodeIdentity(NS, "table", "orders")
    path = PropertyPath.parse("/description")
    result = PropertyConflictResult(
        object_identity=identity,
        property_path=path,
        state="INVALID_OR_AMBIGUOUS_AUTHORITY",
        reason="INVALID_OR_AMBIGUOUS_AUTHORITY",
        value_groups=(
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="from-odcs",
                provenance=(_prov("odcs", "a"),),
            ),
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="from-dbt",
                provenance=(_prov("dbt", "b"),),
            ),
        ),
    )
    report = PropertyConflictReport(results=(result,))
    assessments = [assess_reconciliation(item) for item in report.results]
    block = build_conflicts_block(
        report=report,
        assessments=assessments,
        observations_content_identity=build_observation_set().content_identity().to_dict(),
        authority_content_identity=NormalizedAuthorityPolicySet().content_identity().to_dict(),
    )
    assert block["status"] == "blocked"
    assert block["summary"]["invalid_or_ambiguous_authority"] == 1
    assert block["summary"]["reconciliation_blocked"] == 1
    validate_review_result(build_review_result(conflicts=block))


def test_conflict_unsupported_effective_value_blocks() -> None:
    identity = GraphNodeIdentity(NS, "table", "orders")
    path = PropertyPath.parse("/description")
    result = PropertyConflictResult(
        object_identity=identity,
        property_path=path,
        state="SINGLE_OBSERVATION",
        reason="SINGLE_OBSERVATION",
        value_groups=(
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="present",
                provenance=(_prov("odcs", "a"),),
            ),
        ),
        has_effective_value=False,
    )
    assessment = assess_reconciliation(result)
    assert assessment.reason == "unsupported_effective_value"
    assert assessment.applicable is True
    assert assessment.safe is False
    report = PropertyConflictReport(results=(result,))
    block = build_conflicts_block(
        report=report,
        assessments=[assessment],
        observations_content_identity=build_observation_set().content_identity().to_dict(),
        authority_content_identity=NormalizedAuthorityPolicySet().content_identity().to_dict(),
    )
    assert block["status"] == "blocked"
    assert block["summary"]["reconciliation_blocked"] == 1
    validate_review_result(build_review_result(conflicts=block))


def test_conflict_report_identity_exact_match() -> None:
    observations = build_observation_set()
    authority = NormalizedAuthorityPolicySet()
    report = analyze_property_conflicts(observations, authority)
    assessments = [assess_reconciliation(item) for item in report.results]
    block = build_conflicts_block(
        report=report,
        assessments=assessments,
        observations_content_identity=observations.content_identity().to_dict(),
        authority_content_identity=authority.content_identity().to_dict(),
    )
    assert block["conflict_report"] == report.to_identity_dict()
    assert block["conflict_report_content_identity"] == report.content_identity().to_dict()


def test_assessments_align_one_to_one_object_property() -> None:
    block = _clear_conflicts_block()
    results = block["conflict_report"]["results"]
    assessments = block["reconciliation_assessments"]
    assert len(results) == len(assessments)
    for result, assessment in zip(results, assessments, strict=True):
        assert assessment["object"] == result["object"]
        assert assessment["property"] == result["property"]


# --- V2 Blocker 4C — Drift baseline/candidate -------------------------------


def test_drift_baseline_candidate_identical_no_difference(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_sample_snapshot(workspace / "base.json")
    write_sample_snapshot(workspace / "cand.json")
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_baseline_snapshot="base.json",
                review_candidate_snapshot="cand.json",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["drift-status"] == "no_difference"
    assert values["review-status"] == "passed"


def test_drift_baseline_candidate_expected_with_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_sample_snapshot(workspace / "base.json", description=None)
    write_sample_snapshot(workspace / "cand.json", description="updated")
    write_full_description_policy(workspace / "policy.yaml")
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_baseline_snapshot="base.json",
                review_candidate_snapshot="cand.json",
                review_drift_policy="policy.yaml",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["drift-status"] == "expected_difference"
    assert values["review-status"] == "passed"


def test_drift_baseline_candidate_unexpected_empty_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_sample_snapshot(workspace / "base.json", description=None)
    write_sample_snapshot(workspace / "cand.json", description="updated")
    write_policy(
        workspace / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
""",
    )
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_baseline_snapshot="base.json",
                review_candidate_snapshot="cand.json",
                review_drift_policy="policy.yaml",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["drift-status"] == "unexpected_drift"
    assert values["review-status"] == "blocked"


def test_drift_root_mismatch_without_ack_failed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_sample_snapshot(workspace / "base.json", source_name="dev", database_name="dev_db")
    write_sample_snapshot(
        workspace / "cand.json",
        source_name="prod",
        database_name="prod_db",
        description="x",
    )
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_baseline_snapshot="base.json",
                review_candidate_snapshot="cand.json",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "failed"
    assert values["review-status"] == "failed"
    assert values["desired-exit-code"] == "1"
    assert values.get("review-result-path", "") == ""


def test_drift_root_mismatch_with_align_passed_expected(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_sample_snapshot(
        workspace / "base.json",
        source_name="dev",
        database_name="dev_db",
        description=None,
    )
    write_sample_snapshot(
        workspace / "cand.json",
        source_name="prod",
        database_name="prod_db",
        description="updated",
    )
    write_policy(
        workspace / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: allow-source-name
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /name
  - id: allow-database-name
    match:
      change: changed
      object:
        kind: database
        path: []
      property: /name
  - id: allow-source-description
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
  - id: allow-table-description
    match:
      change: changed
      object:
        kind: table
        path: ["sales", "orders"]
      property: /description
""",
    )
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_baseline_snapshot="base.json",
                review_candidate_snapshot="cand.json",
                review_drift_policy="policy.yaml",
                review_align_source_roots="true",
                review_align_database_roots="true",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["drift-status"] == "expected_difference"
    assert values["review-status"] == "passed"


def test_drift_incompatible_scanner_failed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_sample_snapshot(workspace / "base.json", scanner="postgresql")
    write_sample_snapshot(workspace / "cand.json", description="x", scanner="other")
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_baseline_snapshot="base.json",
                review_candidate_snapshot="cand.json",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "failed"
    assert values["review-status"] == "failed"
    assert values.get("review-result-path", "") == ""


def test_drift_tampered_comparison_failed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_identical_comparison(workspace / "cmp.json")
    payload = json.loads((workspace / "cmp.json").read_text(encoding="utf-8"))
    payload["content_identity"] = dict(payload["content_identity"])
    payload["content_identity"]["digest"] = "0" * 64
    (workspace / "cmp.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_comparison="cmp.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "failed"
    assert values["review-status"] == "failed"
    assert values.get("review-result-path", "") == ""


def test_drift_precomputed_vs_baseline_candidate_same_status(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_identical_comparison(workspace / "cmp.json")
    write_sample_snapshot(workspace / "base.json")
    write_sample_snapshot(workspace / "cand.json")

    out_pre = tmp_path / "github_output_pre"
    env_pre = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out_pre),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step_pre.md"),
    }
    with patch.dict(os.environ, env_pre, clear=False):
        code_pre = run_orchestration(
            _review_args(review_comparison="cmp.json", output_directory=".governance-pre"),
            stdout=io.StringIO(),
        )
    values_pre = _read_github_output(out_pre)

    out_pair = tmp_path / "github_output_pair"
    env_pair = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out_pair),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step_pair.md"),
    }
    with patch.dict(os.environ, env_pair, clear=False):
        code_pair = run_orchestration(
            _review_args(
                review_baseline_snapshot="base.json",
                review_candidate_snapshot="cand.json",
                output_directory=".governance-pair",
            ),
            stdout=io.StringIO(),
        )
    values_pair = _read_github_output(out_pair)

    assert code_pre == 0
    assert code_pair == 0
    assert values_pre["drift-status"] == values_pair["drift-status"] == "no_difference"
    assert values_pre["review-status"] == values_pair["review-status"] == "passed"


# --- V2 Blocker 4D — Combined -----------------------------------------------


def test_combined_clear_and_expected_passed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", build_observation_set())
    write_different_description_comparison(workspace / "cmp.json")
    write_full_description_policy(workspace / "policy.yaml")
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_observations="obs.json",
                review_comparison="cmp.json",
                review_drift_policy="policy.yaml",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["conflict-status"] == "clear"
    assert values["drift-status"] == "expected_difference"
    assert values["review-status"] == "passed"


def test_combined_blocked_and_no_difference_blocked(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", _conflicting_observations())
    write_identical_comparison(workspace / "cmp.json")
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_observations="obs.json",
                review_comparison="cmp.json",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["conflict-status"] == "blocked"
    assert values["drift-status"] == "no_difference"
    assert values["review-status"] == "blocked"


def test_combined_clear_and_unexpected_blocked(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", build_observation_set())
    write_different_description_comparison(workspace / "cmp.json")
    write_policy(
        workspace / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
""",
    )
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_observations="obs.json",
                review_comparison="cmp.json",
                review_drift_policy="policy.yaml",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["conflict-status"] == "clear"
    assert values["drift-status"] == "unexpected_drift"
    assert values["review-status"] == "blocked"


def test_combined_both_blockers_blocked(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(workspace / "obs.json", _conflicting_observations())
    write_different_description_comparison(workspace / "cmp.json")
    write_policy(
        workspace / "policy.yaml",
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
""",
    )
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_observations="obs.json",
                review_comparison="cmp.json",
                review_drift_policy="policy.yaml",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["conflict-status"] == "blocked"
    assert values["drift-status"] == "unexpected_drift"
    assert values["review-status"] == "blocked"


def test_combined_non_mapped_findings_and_expected_passed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_observations(
        workspace / "obs.json",
        _conflicting_observations(property_pointer="/attributes/foo"),
    )
    write_different_description_comparison(workspace / "cmp.json")
    write_full_description_policy(workspace / "policy.yaml")
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(
                review_observations="obs.json",
                review_comparison="cmp.json",
                review_drift_policy="policy.yaml",
            ),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["conflict-status"] == "findings"
    assert values["drift-status"] == "expected_difference"
    assert values["review-status"] == "passed"


def test_combined_lane_failure_invalid_observations_json_failed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "obs.json").write_text("{not-json", encoding="utf-8")
    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_observations="obs.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "failed"
    assert values["review-status"] == "failed"
    assert values.get("review-result-path", "") == ""


# --- V2 Blocker 4E — Report bounding ----------------------------------------


def _many_unresolved_conflicts(count: int) -> dict[str, Any]:
    path = PropertyPath.parse("/description")
    results: list[PropertyConflictResult] = []
    for index in range(count):
        identity = GraphNodeIdentity(NS, "table", f"t{index:03d}")
        results.append(
            PropertyConflictResult(
                object_identity=identity,
                property_path=path,
                state="UNRESOLVED_CONFLICT",
                reason="NO_AUTHORITY_RULE",
                value_groups=(
                    PropertyObservation(
                        object_identity=identity,
                        property_path=path,
                        value="a",
                        provenance=(_prov("odcs", "a"),),
                    ),
                    PropertyObservation(
                        object_identity=identity,
                        property_path=path,
                        value="b",
                        provenance=(_prov("dbt", "b"),),
                    ),
                ),
            )
        )
    report = PropertyConflictReport(results=tuple(results))
    assessments = [assess_reconciliation(item) for item in report.results]
    return build_conflicts_block(
        report=report,
        assessments=assessments,
        observations_content_identity=build_observation_set().content_identity().to_dict(),
        authority_content_identity=NormalizedAuthorityPolicySet().content_identity().to_dict(),
    )


def test_report_deterministic_bytes() -> None:
    result = build_review_result(conflicts=_clear_conflicts_block())
    left = render_review_report(
        review_status=result["status"],
        review_result=result,
        conflict_status=result["conflicts"]["status"],
    )
    right = render_review_report(
        review_status=result["status"],
        review_result=result,
        conflict_status=result["conflicts"]["status"],
    )
    assert left.encode("utf-8") == right.encode("utf-8")


def test_report_conflict_items_truncation() -> None:
    count = MAX_REVIEW_CONFLICT_ITEMS + 5
    block = _many_unresolved_conflicts(count)
    result = build_review_result(conflicts=block)
    markdown = render_review_report(
        review_status=result["status"],
        review_result=result,
        conflict_status=block["status"],
        review_result_path=".governance/review-result.json",
    )
    assert f"showing {MAX_REVIEW_CONFLICT_ITEMS} of {count}" in markdown


def test_report_drift_items_truncation(tmp_path: Path) -> None:
    drift, drift_result = _unexpected_drift_block(tmp_path)
    forged_drift_result = dict(drift_result)
    changes = []
    for index in range(MAX_REVIEW_DRIFT_ITEMS + 3):
        changes.append(
            {
                "classification": "unexpected_drift",
                "change": "changed",
                "reason": "no_matching_rule",
                "object_identity": {"kind": "table", "path": ["sales", f"t{index:03d}"]},
                "property": "/description",
            }
        )
    forged_drift_result["classified_changes"] = changes
    result = build_review_result(drift=drift)
    markdown = render_review_report(
        review_status=result["status"],
        review_result=result,
        drift_result=forged_drift_result,
        drift_status=drift["status"],
        drift_result_path=".governance/drift-result.json",
    )
    assert f"showing {MAX_REVIEW_DRIFT_ITEMS} of {len(changes)}" in markdown


def test_annotations_truncation_warning() -> None:
    count = MAX_ANNOTATIONS + 4
    block = _many_unresolved_conflicts(count)
    result = build_review_result(conflicts=block)
    annotations = build_review_annotations(
        review_status=result["status"],
        review_result=result,
    )
    assert len(annotations) == MAX_ANNOTATIONS
    assert any("omitted" in line for line in annotations)
    omitted = count - (MAX_ANNOTATIONS - 1)
    assert any(f"omitted {omitted}" in line for line in annotations)


def test_report_markdown_escapes_backticks() -> None:
    identity = GraphNodeIdentity(NS, "table", "orders`leak")
    path = PropertyPath.parse("/description")
    conflict = PropertyConflictResult(
        object_identity=identity,
        property_path=path,
        state="UNRESOLVED_CONFLICT",
        reason="NO_AUTHORITY_RULE",
        value_groups=(
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="a",
                provenance=(_prov("odcs", "a"),),
            ),
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="b",
                provenance=(_prov("dbt", "b"),),
            ),
        ),
    )
    report = PropertyConflictReport(results=(conflict,))
    assessments = [assess_reconciliation(item) for item in report.results]
    block = build_conflicts_block(
        report=report,
        assessments=assessments,
        observations_content_identity=build_observation_set().content_identity().to_dict(),
        authority_content_identity=NormalizedAuthorityPolicySet().content_identity().to_dict(),
    )
    result = build_review_result(conflicts=block)
    markdown = render_review_report(
        review_status=result["status"],
        review_result=result,
        conflict_status=block["status"],
    )
    assert "orders\\`leak" in markdown
    assert markdown.count("logical_id=") >= 1


def test_annotations_workflow_command_escapes_newlines() -> None:
    identity = GraphNodeIdentity(NS, "table", "orders\ninjected")
    path = PropertyPath.parse("/description")
    conflict = PropertyConflictResult(
        object_identity=identity,
        property_path=path,
        state="UNRESOLVED_CONFLICT",
        reason="NO_AUTHORITY_RULE",
        value_groups=(
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="a",
                provenance=(_prov("odcs", "a"),),
            ),
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="b",
                provenance=(_prov("dbt", "b"),),
            ),
        ),
    )
    report = PropertyConflictReport(results=(conflict,))
    assessments = [assess_reconciliation(item) for item in report.results]
    block = build_conflicts_block(
        report=report,
        assessments=assessments,
        observations_content_identity=build_observation_set().content_identity().to_dict(),
        authority_content_identity=NormalizedAuthorityPolicySet().content_identity().to_dict(),
    )
    result = build_review_result(conflicts=block)
    annotations = build_review_annotations(
        review_status=result["status"],
        review_result=result,
    )
    assert len(annotations) == 1
    assert annotations[0].count("::error::") == 1
    assert "\n" not in annotations[0]
    # format_human_value collapses newlines before workflow escaping
    assert "\\ninjected" in annotations[0]
    assert "::error::injected" not in annotations[0]


def test_report_heading_contains_em_dash_title() -> None:
    markdown = render_review_report(
        review_status="passed",
        review_result=build_review_result(),
    )
    assert "# Governance as Code — Review" in markdown


# --- V2 Blocker 4F — Sentinel complete --------------------------------------


def test_sentinel_effective_value_and_authority_source_ref_complete(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_authority_yaml(workspace / "auth.yaml", source_ref=SENTINEL)
    config = _write_config_with_authority(workspace, "auth.yaml")
    write_observations(
        workspace / "obs.json",
        PropertyObservationSet.from_observations(
            [
                PropertyObservation(
                    object_identity=GraphNodeIdentity(NS, "table", "orders"),
                    property_path=PropertyPath.parse("/description"),
                    value=SENTINEL,
                    provenance=(_prov("odcs", SENTINEL),),
                ),
                PropertyObservation(
                    object_identity=GraphNodeIdentity(NS, "table", "orders"),
                    property_path=PropertyPath.parse("/description"),
                    value="other",
                    provenance=(_prov("dbt", "manifest"),),
                ),
            ]
        ),
    )
    out = tmp_path / "github_output"
    summary = tmp_path / "step_summary.md"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    buf = io.StringIO()
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(config=config, review_observations="obs.json", output_format="human"),
            stdout=buf,
        )
    assert code == 0
    values = _read_github_output(out)
    review_text = (workspace / values["review-result-path"]).read_text(encoding="utf-8")
    assert SENTINEL in review_text
    payload = json.loads(review_text)
    resolved = [
        item
        for item in payload["conflicts"]["conflict_report"]["results"]
        if item["state"] == "RESOLVED_BY_AUTHORITY"
    ]
    assert resolved
    assert resolved[0]["effective_value"] == SENTINEL
    assert resolved[0]["winning_rule_key"]["authority"]["source_ref"] == SENTINEL

    report = (workspace / values["report-path"]).read_text(encoding="utf-8")
    annotations = (workspace / values["annotations-path"]).read_text(encoding="utf-8")
    output_blob = "\n".join(f"{k}={v}" for k, v in values.items())
    step = summary.read_text(encoding="utf-8") if summary.is_file() else ""
    comment = build_comment_body(report)
    captured = capsys.readouterr()
    surfaces = [
        report,
        annotations,
        output_blob,
        step,
        comment,
        buf.getvalue(),
        captured.out,
        captured.err,
    ]
    for surface in surfaces:
        assert SENTINEL not in surface


# --- V2 Blocker 4G — Recursion ----------------------------------------------


def test_load_review_deeply_nested_json_recursion_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "nested.json"
    path.write_text('{"ok": true}\n', encoding="utf-8")

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RecursionError("too deep")

    monkeypatch.setattr("governance.github_ci.review.json.loads", _boom)
    with pytest.raises(ReviewResultError) as exc_info:
        load_review_result_artifact(path)
    assert exc_info.value.errors[0].code == "parse_error"
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_validate_review_identity_recursion_bounded(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = build_review_result()

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RecursionError("too deep")

    monkeypatch.setattr("governance.github_ci.review.ci_review_result_identity", _boom)
    with pytest.raises(ReviewResultError) as exc_info:
        validate_review_result(result)
    assert exc_info.value.errors[0].code == "invalid_artifact"
    assert "deeply nested" in exc_info.value.errors[0].message
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err


# --- V2 Blocker 5 — write_error operational ---------------------------------


def test_write_comparison_write_error_operational_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from governance.comparison.errors import CODE_WRITE_ERROR, ComparisonError, DiagnosticError

    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_identical_comparison(workspace / "cmp.json")

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ComparisonError(
            [
                DiagnosticError(
                    code=CODE_WRITE_ERROR,
                    path="/output",
                    message="unable to write comparison artifact",
                )
            ]
        )

    monkeypatch.setattr("governance.comparison.write_comparison_artifact", _boom)

    out = tmp_path / "github_output"
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_comparison="cmp.json"),
            stdout=io.StringIO(),
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "failed"
    assert values["review-status"] == "failed"
    assert values["desired-exit-code"] == "1"
    assert values.get("review-result-path", "") == ""
    report = (workspace / values["report-path"]).read_text(encoding="utf-8")
    assert "Failure: operational_failure" in report


# --- V3 Blocker 1 — safe JSON console (no machine detail in stdout) ---------


def test_output_format_json_console_omits_sentinel_and_machine_detail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json as _json

    workspace = tmp_path / "ws"
    workspace.mkdir()
    observations = PropertyObservationSet.from_observations(
        [
            PropertyObservation(
                object_identity=GraphNodeIdentity(NS, "table", "orders"),
                property_path=PropertyPath.parse("/description"),
                value=SENTINEL,
                provenance=(_prov("odcs", SENTINEL),),
            )
        ]
    )
    write_observations(workspace / "obs.json", observations)
    buf = io.StringIO()
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_observations="obs.json", output_format="json"),
            stdout=buf,
        )
    assert code == 0
    stdout = buf.getvalue()
    assert SENTINEL not in stdout
    payload = _json.loads(stdout)
    assert payload["operation"] == "review"
    assert payload["writes_performed"] == 0
    assert payload["status"] == "passed"
    assert payload["review_status"] == "passed"
    assert "conflict_report" not in payload
    assert "reconciliation_assessments" not in payload
    assert "source_ref" not in _json.dumps(payload)
    assert SENTINEL not in _json.dumps(payload)
    machine = (workspace / payload["review_result_path"]).read_text(encoding="utf-8")
    assert SENTINEL in machine
    captured = capsys.readouterr()
    assert SENTINEL not in captured.err
    assert "Traceback" not in captured.err


def test_output_format_json_failed_emits_safe_failure_json(tmp_path: Path) -> None:
    import json as _json

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "obs.json").write_text("{not-json", encoding="utf-8")
    out = tmp_path / "github_output"
    buf = io.StringIO()
    env = _run_env(workspace, tmp_path)
    with patch.dict(os.environ, env, clear=False):
        code = run_orchestration(
            _review_args(review_observations="obs.json", output_format="json"),
            stdout=buf,
        )
    assert code == 0
    values = _read_github_output(out)
    assert values["status"] == "failed"
    payload = _json.loads(buf.getvalue())
    assert payload["operation"] == "review"
    assert payload["status"] == "failed"
    assert payload["review_status"] == "failed"
    assert payload["failure_code"] == "configuration_failed"
    assert payload["writes_performed"] == 0
    assert payload["review_result_path"] == ""
    assert "conflict_report" not in payload
    assert "Traceback" not in buf.getvalue()


# --- V3 Blocker 2 — distinguishable parent-aware identity + single-line -----


def test_report_same_state_different_parent_identities_distinct() -> None:
    parent_a = GraphNodeIdentity(NS, "table", "orders")
    parent_b = GraphNodeIdentity(NS, "table", "customers")
    col_a = GraphNodeIdentity(NS, "column", "id", parent=parent_a)
    col_b = GraphNodeIdentity(NS, "column", "id", parent=parent_b)
    path = PropertyPath.parse("/attributes/data_type")
    left = PropertyObservation(
        object_identity=col_a,
        property_path=path,
        value="int",
        provenance=(_prov("odcs", "a"),),
    )
    right_a = PropertyObservation(
        object_identity=col_a,
        property_path=path,
        value="text",
        provenance=(_prov("dbt", "b"),),
    )
    left_b = PropertyObservation(
        object_identity=col_b,
        property_path=path,
        value="int",
        provenance=(_prov("odcs", "c"),),
    )
    right_b = PropertyObservation(
        object_identity=col_b,
        property_path=path,
        value="text",
        provenance=(_prov("dbt", "d"),),
    )
    report = PropertyConflictReport(
        results=(
            PropertyConflictResult(
                object_identity=col_a,
                property_path=path,
                state="UNRESOLVED_CONFLICT",
                reason="NO_AUTHORITY_RULE",
                value_groups=(left, right_a),
            ),
            PropertyConflictResult(
                object_identity=col_b,
                property_path=path,
                state="UNRESOLVED_CONFLICT",
                reason="NO_AUTHORITY_RULE",
                value_groups=(left_b, right_b),
            ),
        )
    )
    assessments = [assess_reconciliation(item) for item in report.results]
    observations = PropertyObservationSet.from_observations([left, right_a, left_b, right_b])
    block = build_conflicts_block(
        report=report,
        assessments=assessments,
        observations_content_identity=observations.content_identity().to_dict(),
        authority_content_identity=NormalizedAuthorityPolicySet().content_identity().to_dict(),
    )
    result = build_review_result(conflicts=block)
    markdown = render_review_report(
        review_status=result["status"],
        review_result=result,
        conflict_status=block["status"],
    )
    assert "object_identity=" in markdown
    assert "object_identity_digest=" in markdown
    assert f"{NS}/column/id under {NS}/table/orders" in markdown
    assert f"{NS}/column/id under {NS}/table/customers" in markdown
    digests = [
        line.split("object_identity_digest=")[1].split()[0]
        for line in markdown.splitlines()
        if "object_identity_digest=" in line
    ]
    assert len(digests) == 2
    assert digests[0] != digests[1]
    assert "\nINJECTED" not in markdown


def test_report_control_chars_in_logical_id_remain_single_line() -> None:
    identity = GraphNodeIdentity(NS, "table", "orders\nINJECTED_HEADING")
    path = PropertyPath.parse("/description")
    obs = PropertyObservation(
        object_identity=identity,
        property_path=path,
        value="x",
        provenance=(_prov("odcs", "a"),),
    )
    result_item = PropertyConflictResult(
        object_identity=identity,
        property_path=path,
        state="UNRESOLVED_CONFLICT",
        reason="NO_AUTHORITY_RULE",
        value_groups=(
            obs,
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="y",
                provenance=(_prov("dbt", "b"),),
            ),
        ),
    )
    report = PropertyConflictReport(results=(result_item,))
    assessments = [assess_reconciliation(item) for item in report.results]
    observations = PropertyObservationSet.from_observations(list(result_item.value_groups))
    block = build_conflicts_block(
        report=report,
        assessments=assessments,
        observations_content_identity=observations.content_identity().to_dict(),
        authority_content_identity=NormalizedAuthorityPolicySet().content_identity().to_dict(),
    )
    review = build_review_result(conflicts=block)
    markdown = render_review_report(
        review_status=review["status"],
        review_result=review,
        conflict_status=block["status"],
    )
    assert "INJECTED_HEADING" in markdown
    assert "\nINJECTED_HEADING" not in markdown
    assert "\\nINJECTED_HEADING" in markdown
    # Title is line 1 (`# ...`); no injected ATX heading on a later line.
    assert markdown.count("\n# ") == 0
    assert markdown.startswith("# ")
    conflict_lines = [line for line in markdown.splitlines() if "logical_id=" in line]
    assert len(conflict_lines) == 1
    assert "\\nINJECTED_HEADING" in conflict_lines[0]
    assert "\n" not in conflict_lines[0]


def test_report_repeat_render_byte_identical_with_object_identity() -> None:
    block = _clear_conflicts_block()
    review = build_review_result(conflicts=block)
    left = render_review_report(
        review_status=review["status"],
        review_result=review,
        conflict_status=block["status"],
    )
    right = render_review_report(
        review_status=review["status"],
        review_result=review,
        conflict_status=block["status"],
    )
    assert left == right
    assert left.encode("utf-8") == right.encode("utf-8")


# --- V3 Blocker 3 — in-memory NaN/Infinity rejected -------------------------


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_validate_and_write_reject_non_finite_json_values(tmp_path: Path, bad_value: float) -> None:
    from governance.identity.hashing import (
        ci_review_result_identity,
        property_conflict_report_identity,
    )

    block = _clear_conflicts_block()
    report = dict(block["conflict_report"])
    results = [dict(item) for item in report["results"]]
    first = dict(results[0])
    first["effective_value"] = bad_value
    results[0] = first
    report["results"] = results
    block = dict(block)
    block["conflict_report"] = report
    block["conflict_report_content_identity"] = property_conflict_report_identity(report).to_dict()
    payload = build_review_result(conflicts=block)
    without = {key: value for key, value in payload.items() if key != "content_identity"}
    without["conflicts"] = block
    payload = dict(without)
    payload["content_identity"] = ci_review_result_identity(without).to_dict()

    with pytest.raises(ReviewResultError) as exc_info:
        validate_review_result(payload)
    assert exc_info.value.errors[0].code == "invalid_artifact"

    target = tmp_path / "out.json"
    with pytest.raises(ReviewResultError):
        write_review_result(payload, target)
    assert not target.exists()
    # canonical path must not emit non-standard tokens when somehow reached
    with pytest.raises(ValueError):
        canonical_review_json(payload)


def test_load_review_nan_literal_still_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{"review_schema":"governance-ci-review-result","x":NaN}\n', encoding="utf-8")
    with pytest.raises(ReviewResultError) as exc_info:
        load_review_result_artifact(path)
    assert exc_info.value.errors[0].code == "parse_error"
