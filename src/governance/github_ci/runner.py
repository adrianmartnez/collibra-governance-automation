"""Orchestrate read-only governance CLI runs for the official GitHub Action."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from governance.github_ci.paths import PathValidationError, WorkspacePaths
from governance.github_ci.report import (
    build_annotations,
    build_impact_annotations,
    build_review_annotations,
    render_human_summary,
    render_impact_human_summary,
    render_impact_report,
    render_report,
    render_review_human_summary,
    render_review_report,
    write_annotations_file,
    write_report_file,
)
from governance.github_ci.result import (
    ACTION_RESULT_NAME,
    CONFIG_RESULT_NAME,
    IMPACT_RESULT_NAME,
    IMPACT_RESULT_VERSION,
    PLAN_RESULT_NAME,
    POLICY_RESULT_NAME,
    RESULT_VERSION,
    CliContractError,
    action_result_outputs,
    build_action_result,
    check_plan_identity_consistency,
    count_plan_actions,
    count_policy_violations,
    empty_plan,
    empty_policy,
    empty_validation,
    load_recognized_impact_result,
    parse_cli_payload,
    parse_impact_stdout_diagnostic,
    write_action_result,
    write_canonical_json,
)
from governance.impact import ImpactError, canonical_impact_json, format_human_value

_SCRUB_ENV_KEYS = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "INPUT_GITHUB_TOKEN",
    }
)

_PHASE_A_STDERR = "invalid action output directory"

_SOURCE_KIND_FLAGS = (
    ("dbt", "--dbt-manifest"),
    ("odcs", "--odcs"),
    ("openlineage", "--openlineage"),
)


@dataclass(slots=True)
class ImpactRunState:
    """Private in-memory orchestration state for operation=impact (not a public contract)."""

    status: str
    impact_status: str
    failure_code: str | None
    impact_result_path: str
    impact_result_version: str
    desired_exit_code: int
    writes_performed: int = 0
    impact_result: dict[str, Any] | None = None
    diagnostic: dict[str, Any] | None = None
    diagnostic_errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ReviewRunState:
    """Private in-memory orchestration state for operation=review (not a public contract)."""

    status: str
    review_status: str
    conflict_status: str
    drift_status: str
    failure_code: str | None
    review_result_path: str
    review_result_version: str
    comparison_result_path: str
    drift_result_path: str
    conflict_property_count: str
    unresolved_conflict_count: str
    resolved_authority_count: str
    reconciliation_blocked_count: str
    expected_difference_count: str
    unexpected_drift_count: str
    drift_affected_object_count: str
    desired_exit_code: int
    review_result: dict[str, Any] | None = None
    drift_result: dict[str, Any] | None = None


class ActionInputContractError(ValueError):
    """Invalid Action input contract (Phase A)."""


def _review_output_defaults(*, operation: str = "") -> dict[str, str]:
    return {
        "review-status": "failed" if operation == "review" else "not_run",
        "review-result-path": "",
        "review-result-version": "",
        "conflict-status": "not_run",
        "conflict-property-count": "0",
        "unresolved-conflict-count": "0",
        "resolved-authority-count": "0",
        "reconciliation-blocked-count": "0",
        "drift-status": "not_run",
        "expected-difference-count": "0",
        "unexpected-drift-count": "0",
        "drift-affected-object-count": "0",
        "comparison-result-path": "",
        "drift-result-path": "",
    }


def _failed_review_state(failure_code: str) -> ReviewRunState:
    return ReviewRunState(
        status="failed",
        review_status="failed",
        conflict_status="not_run",
        drift_status="not_run",
        failure_code=failure_code,
        review_result_path="",
        review_result_version="",
        comparison_result_path="",
        drift_result_path="",
        conflict_property_count="0",
        unresolved_conflict_count="0",
        resolved_authority_count="0",
        reconciliation_blocked_count="0",
        expected_difference_count="0",
        unexpected_drift_count="0",
        drift_affected_object_count="0",
        desired_exit_code=1,
    )


def _reject_path_controls(raw: str, *, field: str) -> None:
    if "\x00" in raw or "\r" in raw or "\n" in raw:
        raise ActionInputContractError(f"{field} must not contain NUL, CR, or LF characters")


def parse_source_path_array(raw: str, *, field: str) -> list[str]:
    """Parse a JSON array of workspace-relative path strings for impact sources."""
    text = raw if isinstance(raw, str) else ""
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ActionInputContractError(f"{field} must be a JSON array of strings") from exc
    if not isinstance(payload, list):
        raise ActionInputContractError(f"{field} must be a JSON array of strings")
    paths: list[str] = []
    for index, item in enumerate(payload):
        if not isinstance(item, str):
            raise ActionInputContractError(f"{field}[{index}] must be a string")
        if item == "":
            raise ActionInputContractError(f"{field}[{index}] must be a non-empty path")
        if "\x00" in item or "\r" in item or "\n" in item:
            raise ActionInputContractError(
                f"{field}[{index}] must not contain NUL, CR, or LF characters"
            )
        paths.append(item)
    return paths


def _parse_bool(raw: str, *, field: str) -> bool:
    value = raw.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{field} must be true or false")


def write_github_output(outputs: Mapping[str, str], env: Mapping[str, str] | None = None) -> None:
    """Append scalar outputs to GITHUB_OUTPUT when present."""
    source = env if env is not None else os.environ
    path_raw = source.get("GITHUB_OUTPUT", "").strip()
    if not path_raw:
        return
    path = Path(path_raw)
    lines: list[str] = []
    for key, value in outputs.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"output {key} must be a scalar without newlines")
        lines.append(f"{key}={value}")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")


def scrubbed_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    source = dict(base if base is not None else os.environ)
    for key in _SCRUB_ENV_KEYS:
        source.pop(key, None)
    # Also scrub any Action-internal comment token variable if present.
    source.pop("GOVERNANCE_COMMENT_TOKEN", None)
    return source


def build_cli_argv(
    *,
    executable: str,
    args: Sequence[str],
) -> list[str]:
    return [executable, "-I", "-m", "governance", *args]


def run_governance_cli(
    argv_tail: Sequence[str],
    *,
    workspace: Path,
    env: Mapping[str, str] | None = None,
    executable: str | None = None,
) -> subprocess.CompletedProcess[str]:
    exe = executable or sys.executable
    return subprocess.run(
        build_cli_argv(executable=exe, args=argv_tail),
        cwd=str(workspace),
        env=scrubbed_env(env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
    )


def desired_exit_code(*, status: str, fail_on_policy_error: bool) -> int:
    if status == "passed":
        return 0
    if status == "blocked":
        return 3 if fail_on_policy_error else 0
    return 1


def _phase_a_outputs(*, operation: str = "") -> dict[str, str]:
    contract_version = "" if operation in {"impact", "review"} else RESULT_VERSION
    outputs = {
        "contract-version": contract_version,
        "status": "failed",
        "validation-status": "not_run",
        "policy-status": "not_run",
        "policy-violation-count": "0",
        "policy-error-count": "0",
        "policy-warning-count": "0",
        "plan-status": "not_run",
        "create-count": "0",
        "update-count": "0",
        "unchanged-count": "0",
        "remote-only-count": "0",
        "writes-performed": "0",
        "plan-path": "",
        "result-path": "",
        "report-path": "",
        "artifacts-path": "",
        "impact-status": "not_run",
        "impact-result-path": "",
        "impact-result-version": "",
        "desired-exit-code": "1",
        "phase-a-failed": "true",
        "annotations-path": "",
    }
    outputs.update(_review_output_defaults(operation=operation))
    return outputs


def _append_profile(argv: list[str], profile: str) -> None:
    if profile.strip():
        argv.extend(["--profile", profile.strip()])


def _write_step_summary(report_text: str, env: Mapping[str, str]) -> None:
    summary = env.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary:
        return
    with Path(summary).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(report_text)


def _materialize_phase_b(
    *,
    output_rel: str,
    output_abs: Path,
    action_result: dict[str, Any],
    policy_report: dict[str, Any] | None,
    plan_dict: dict[str, Any] | None,
    fail_on_policy_error: bool,
    output_format: str,
    stdout: TextIO,
) -> int:
    output_abs.mkdir(parents=True, exist_ok=True)
    action_path = output_abs / ACTION_RESULT_NAME
    result_bytes = write_action_result(action_path, action_result)
    report_text = render_report(
        action_result,
        policy_report,
        plan_dict,
        artifacts_relative=output_rel,
    )
    write_report_file(output_abs, report_text)
    annotations = build_annotations(action_result, policy_report)
    write_annotations_file(output_abs, annotations)
    _write_step_summary(report_text, os.environ)

    result_rel = f"{output_rel}/{ACTION_RESULT_NAME}"
    report_rel = f"{output_rel}/report.md"
    annotations_rel = f"{output_rel}/annotations.txt"
    plan_path_out = action_result["plan"]["plan_path"] or ""

    outputs = action_result_outputs(action_result)
    outputs.update(
        {
            "result-path": result_rel,
            "report-path": report_rel,
            "artifacts-path": output_rel,
            "plan-path": "" if not plan_path_out else str(plan_path_out),
            "desired-exit-code": str(
                desired_exit_code(
                    status=str(action_result["status"]),
                    fail_on_policy_error=fail_on_policy_error,
                )
            ),
            "phase-a-failed": "false",
            "annotations-path": annotations_rel,
        }
    )
    write_github_output(outputs)

    if output_format == "json":
        stdout.write(result_bytes.decode("utf-8"))
    else:
        stdout.write(render_human_summary(action_result))
    return 0


def _materialize_impact_phase_b(
    *,
    output_rel: str,
    output_abs: Path,
    state: ImpactRunState,
    output_format: str,
    stdout: TextIO,
) -> int:
    output_abs.mkdir(parents=True, exist_ok=True)
    report_text = render_impact_report(
        impact_status=state.impact_status,
        impact_result=state.impact_result,
        impact_result_path=state.impact_result_path or None,
        failure_code=state.failure_code,
        diagnostic=state.diagnostic,
        artifacts_relative=output_rel,
    )
    write_report_file(output_abs, report_text)
    annotations = build_impact_annotations(
        impact_status=state.impact_status,
        impact_result=state.impact_result,
        failure_code=state.failure_code,
    )
    write_annotations_file(output_abs, annotations)
    _write_step_summary(report_text, os.environ)

    report_rel = f"{output_rel}/report.md"
    annotations_rel = f"{output_rel}/annotations.txt"
    outputs = {
        "contract-version": "",
        "status": state.status,
        "validation-status": "not_run",
        "policy-status": "not_run",
        "policy-violation-count": "0",
        "policy-error-count": "0",
        "policy-warning-count": "0",
        "plan-status": "not_run",
        "create-count": "0",
        "update-count": "0",
        "unchanged-count": "0",
        "remote-only-count": "0",
        "writes-performed": "0",
        "plan-path": "",
        "result-path": "",
        "report-path": report_rel,
        "artifacts-path": output_rel,
        "impact-status": state.impact_status,
        "impact-result-path": state.impact_result_path,
        "impact-result-version": state.impact_result_version,
        "desired-exit-code": str(state.desired_exit_code),
        "phase-a-failed": "false",
        "annotations-path": annotations_rel,
    }
    outputs.update(_review_output_defaults(operation="impact"))
    write_github_output(outputs)

    if output_format == "json":
        if state.impact_result is not None:
            stdout.write(canonical_impact_json(state.impact_result))
        elif state.diagnostic is not None:
            stdout.write(
                json.dumps(
                    state.diagnostic,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
                + "\n"
            )
        else:
            # No public machine surface: keep stdout empty; never serialize ImpactRunState.
            if state.failure_code:
                print(
                    format_human_value(f"governance impact failed: {state.failure_code}"),
                    file=sys.stderr,
                )
    else:
        stdout.write(
            render_impact_human_summary(
                impact_status=state.impact_status,
                impact_result=state.impact_result,
                impact_result_path=state.impact_result_path or None,
                failure_code=state.failure_code,
            )
        )
    return 0


def _validate_contained_path(
    paths: WorkspacePaths,
    raw: str,
    *,
    field: str,
) -> str:
    relative = paths.normalize_relative(raw, field=field)
    paths.resolve_under_workspace(relative, field=field)
    return relative


def _parse_impact_sources(
    paths: WorkspacePaths,
    *,
    odcs_raw: str,
    dbt_raw: str,
    openlineage_raw: str,
) -> list[tuple[str, str]]:
    """Return canonical sorted (kind, relative_path) source specs."""
    kind_paths: dict[str, list[str]] = {
        "odcs": parse_source_path_array(odcs_raw, field="impact-odcs"),
        "dbt": parse_source_path_array(dbt_raw, field="impact-dbt-manifest"),
        "openlineage": parse_source_path_array(openlineage_raw, field="impact-openlineage"),
    }
    specs: list[tuple[str, str]] = []
    for kind, raw_paths in kind_paths.items():
        seen: set[str] = set()
        normalized: list[str] = []
        field_name = {
            "odcs": "impact-odcs",
            "dbt": "impact-dbt-manifest",
            "openlineage": "impact-openlineage",
        }[kind]
        for raw_path in raw_paths:
            relative = _validate_contained_path(paths, raw_path, field=field_name)
            if relative in seen:
                raise ActionInputContractError(f"{field_name} contains duplicate path {relative}")
            seen.add(relative)
            normalized.append(relative)
        for relative in normalized:
            specs.append((kind, relative))
    if not specs:
        raise ActionInputContractError(
            "impact requires at least one ODCS, dbt-manifest, or OpenLineage source"
        )
    specs.sort(key=lambda item: (item[0], item[1]))
    return specs


def _build_impact_cli_argv(
    *,
    namespace: str,
    changes_rel: str,
    output_rel: str,
    sources: Sequence[tuple[str, str]],
    dbt_default_database: str,
    config_rel: str | None,
    profile: str,
) -> list[str]:
    argv = [
        "impact",
        "--namespace",
        namespace,
        "--changes",
        changes_rel,
        "--output",
        output_rel,
        "--format",
        "json",
    ]
    flag_by_kind = {kind: flag for kind, flag in _SOURCE_KIND_FLAGS}
    for kind, relative in sources:
        argv.extend([flag_by_kind[kind], relative])
    if dbt_default_database.strip():
        argv.extend(["--dbt-default-database", dbt_default_database.strip()])
    if config_rel is not None:
        argv.extend(["--config", config_rel])
        _append_profile(argv, profile)
    return argv


def _run_impact_operation(
    *,
    paths: WorkspacePaths,
    args: argparse.Namespace,
    output_rel: str,
    output_abs: Path,
    sources: Sequence[tuple[str, str]],
    changes_rel: str,
    config_rel: str | None,
    output_format: str,
    stdout: TextIO,
) -> int:
    impact_out_rel = f"{output_rel}/{IMPACT_RESULT_NAME}"
    impact_out_abs = output_abs / IMPACT_RESULT_NAME
    argv = _build_impact_cli_argv(
        namespace=args.impact_namespace.strip(),
        changes_rel=changes_rel,
        output_rel=impact_out_rel,
        sources=sources,
        dbt_default_database=args.dbt_default_database or "",
        config_rel=config_rel,
        profile=args.profile or "",
    )
    completed = run_governance_cli(argv, workspace=paths.workspace)

    if completed.returncode in {0, 6}:
        try:
            payload = load_recognized_impact_result(impact_out_abs)
        except ImpactError:
            state = ImpactRunState(
                status="failed",
                impact_status="failed",
                failure_code="action_contract_invalid",
                impact_result_path="",
                impact_result_version="",
                desired_exit_code=1,
            )
            return _materialize_impact_phase_b(
                output_rel=output_rel,
                output_abs=output_abs,
                state=state,
                output_format=output_format,
                stdout=stdout,
            )
        impact_status = "impacted" if completed.returncode == 6 else "clear"
        if str(payload.get("status")) != impact_status:
            state = ImpactRunState(
                status="failed",
                impact_status="failed",
                failure_code="action_contract_invalid",
                impact_result_path="",
                impact_result_version="",
                desired_exit_code=1,
            )
            return _materialize_impact_phase_b(
                output_rel=output_rel,
                output_abs=output_abs,
                state=state,
                output_format=output_format,
                stdout=stdout,
            )
        state = ImpactRunState(
            status="passed",
            impact_status=impact_status,
            failure_code=None,
            impact_result_path=impact_out_rel,
            impact_result_version=IMPACT_RESULT_VERSION,
            desired_exit_code=0,
            impact_result=payload,
        )
        return _materialize_impact_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            state=state,
            output_format=output_format,
            stdout=stdout,
        )

    stdout_raw = completed.stdout or ""
    stdout_nonempty = bool(stdout_raw.strip())
    diagnostic: dict[str, Any] | None = None
    parse_error = False
    if stdout_nonempty:
        try:
            diagnostic = parse_impact_stdout_diagnostic(stdout_raw)
        except CliContractError:
            parse_error = True
            diagnostic = None

    code = completed.returncode
    if code == 2:
        failure_code = "action_contract_invalid"
    elif code == 4:
        # Exit 4 requires a recognized diagnostic machine surface.
        failure_code = (
            "configuration_failed" if diagnostic is not None else "action_contract_invalid"
        )
    elif code == 1:
        if not stdout_nonempty:
            failure_code = "operational_failure"
        elif parse_error or diagnostic is None:
            failure_code = "action_contract_invalid"
        else:
            failure_code = "operational_failure"
    else:
        if parse_error or (stdout_nonempty and diagnostic is None):
            failure_code = "action_contract_invalid"
        else:
            failure_code = "operational_failure"

    state = ImpactRunState(
        status="failed",
        impact_status="failed",
        failure_code=failure_code,
        impact_result_path="",
        impact_result_version="",
        desired_exit_code=1,
        diagnostic=diagnostic,
    )
    return _materialize_impact_phase_b(
        output_rel=output_rel,
        output_abs=output_abs,
        state=state,
        output_format=output_format,
        stdout=stdout,
    )


def _materialize_review_phase_b(
    *,
    output_rel: str,
    output_abs: Path,
    state: ReviewRunState,
    output_format: str,
    stdout: TextIO,
) -> int:
    from governance.github_ci.review import build_review_console_json

    output_abs.mkdir(parents=True, exist_ok=True)
    report_text = render_review_report(
        review_status=state.review_status,
        review_result=state.review_result,
        drift_result=state.drift_result,
        failure_code=state.failure_code,
        review_result_path=state.review_result_path or None,
        comparison_result_path=state.comparison_result_path or None,
        drift_result_path=state.drift_result_path or None,
        conflict_status=state.conflict_status,
        drift_status=state.drift_status,
        artifacts_relative=output_rel,
    )
    write_report_file(output_abs, report_text)
    annotations = build_review_annotations(
        review_status=state.review_status,
        review_result=state.review_result,
        drift_result=state.drift_result,
        failure_code=state.failure_code,
    )
    write_annotations_file(output_abs, annotations)
    _write_step_summary(report_text, os.environ)

    report_rel = f"{output_rel}/report.md"
    annotations_rel = f"{output_rel}/annotations.txt"
    outputs = {
        "contract-version": "",
        "status": state.status,
        "validation-status": "not_run",
        "policy-status": "not_run",
        "policy-violation-count": "0",
        "policy-error-count": "0",
        "policy-warning-count": "0",
        "plan-status": "not_run",
        "create-count": "0",
        "update-count": "0",
        "unchanged-count": "0",
        "remote-only-count": "0",
        "writes-performed": "0",
        "plan-path": "",
        "result-path": "",
        "report-path": report_rel,
        "artifacts-path": output_rel,
        "impact-status": "not_run",
        "impact-result-path": "",
        "impact-result-version": "",
        "review-status": state.review_status,
        "review-result-path": state.review_result_path,
        "review-result-version": state.review_result_version,
        "conflict-status": state.conflict_status,
        "conflict-property-count": state.conflict_property_count,
        "unresolved-conflict-count": state.unresolved_conflict_count,
        "resolved-authority-count": state.resolved_authority_count,
        "reconciliation-blocked-count": state.reconciliation_blocked_count,
        "drift-status": state.drift_status,
        "expected-difference-count": state.expected_difference_count,
        "unexpected-drift-count": state.unexpected_drift_count,
        "drift-affected-object-count": state.drift_affected_object_count,
        "comparison-result-path": state.comparison_result_path,
        "drift-result-path": state.drift_result_path,
        "desired-exit-code": str(state.desired_exit_code),
        "phase-a-failed": "false",
        "annotations-path": annotations_rel,
    }
    write_github_output(outputs)

    if output_format == "json":
        stdout.write(
            build_review_console_json(
                status=state.status,
                review_status=state.review_status,
                conflict_status=state.conflict_status,
                drift_status=state.drift_status,
                conflict_property_count=state.conflict_property_count,
                unresolved_conflict_count=state.unresolved_conflict_count,
                resolved_authority_count=state.resolved_authority_count,
                reconciliation_blocked_count=state.reconciliation_blocked_count,
                expected_difference_count=state.expected_difference_count,
                unexpected_drift_count=state.unexpected_drift_count,
                drift_affected_object_count=state.drift_affected_object_count,
                review_result_path=state.review_result_path,
                review_result_version=state.review_result_version,
                comparison_result_path=state.comparison_result_path,
                drift_result_path=state.drift_result_path,
                failure_code=state.failure_code,
            )
        )
    else:
        stdout.write(
            render_review_human_summary(
                review_status=state.review_status,
                review_result=state.review_result,
                failure_code=state.failure_code,
                review_result_path=state.review_result_path or None,
            )
        )
    return 0


def _validate_review_input_path(
    paths: WorkspacePaths,
    raw: str,
    *,
    field: str,
) -> tuple[str, Path]:
    _reject_path_controls(raw, field=field)
    relative = paths.normalize_relative(raw, field=field)
    absolute = paths.resolve_under_workspace(relative, field=field)
    return relative, absolute


def _run_review_operation(
    *,
    paths: WorkspacePaths,
    args: argparse.Namespace,
    output_rel: str,
    output_abs: Path,
    conflict_lane: bool,
    drift_lane: bool,
    observations_rel: str,
    comparison_rel: str,
    baseline_rel: str,
    candidate_rel: str,
    drift_policy_rel: str,
    config_rel: str | None,
    align_source_roots: bool,
    align_database_roots: bool,
    fail_on_review_blocked: bool,
    output_format: str,
    stdout: TextIO,
) -> int:
    from governance.authority.errors import AuthorityError
    from governance.authority.load import load_normalized_authority
    from governance.comparison import (
        ComparisonArtifactError,
        ComparisonError,
        RootAlignmentAck,
        build_comparison_result,
        load_comparison_artifact,
        write_comparison_artifact,
    )
    from governance.config_contract import ConfigContractError, load_canonical_config
    from governance.config_contract.resolve import ConfigResolutionError
    from governance.domain.authority import NormalizedAuthorityPolicySet
    from governance.domain.conflicts import analyze_property_conflicts
    from governance.drift import (
        DriftError,
        build_drift_result,
        load_drift_policy,
        write_drift_artifact,
    )
    from governance.github_ci.review import (
        COMPARISON_RESULT_NAME,
        DRIFT_RESULT_NAME,
        REVIEW_RESULT_NAME,
        REVIEW_VERSION,
        ReviewResultError,
        build_conflicts_block,
        build_drift_block,
        build_review_result,
        desired_review_exit_code,
        validate_review_result,
        write_review_result,
    )
    from governance.observations import (
        ObservationsArtifactError,
        load_property_observation_set_artifact,
    )
    from governance.reconciliation.safety import assess_reconciliation
    from governance.snapshots import SnapshotError, load_snapshot

    def _materialize_failure(failure_code: str) -> int:
        return _materialize_review_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            state=_failed_review_state(failure_code),
            output_format=output_format,
            stdout=stdout,
        )

    conflicts_block: dict[str, Any] | None = None
    drift_block: dict[str, Any] | None = None
    drift_result_payload: dict[str, Any] | None = None
    comparison_out_rel = ""
    drift_out_rel = ""

    try:
        if conflict_lane:
            observations = load_property_observation_set_artifact(
                paths.resolve_under_workspace(observations_rel, field="review-observations")
            )
            if config_rel is not None:
                canonical = load_canonical_config(
                    paths.resolve_under_workspace(config_rel, field="config"),
                    profile=(args.profile or "").strip() or None,
                )
                authority = load_normalized_authority(canonical)
            else:
                authority = NormalizedAuthorityPolicySet()
            authority_content_identity = authority.content_identity().to_dict()
            report = analyze_property_conflicts(observations, authority)
            assessments = [assess_reconciliation(result) for result in report.results]
            conflicts_block = build_conflicts_block(
                report=report,
                assessments=assessments,
                observations_content_identity=observations.content_identity().to_dict(),
                authority_content_identity=authority_content_identity,
            )

        if drift_lane:
            comparison_abs = output_abs / COMPARISON_RESULT_NAME
            comparison_out_rel = f"{output_rel}/{COMPARISON_RESULT_NAME}"
            if comparison_rel:
                comparison = load_comparison_artifact(
                    paths.resolve_under_workspace(comparison_rel, field="review-comparison")
                )
                write_comparison_artifact(comparison, comparison_abs)
            else:
                baseline = load_snapshot(
                    paths.resolve_under_workspace(baseline_rel, field="review-baseline-snapshot")
                )
                candidate = load_snapshot(
                    paths.resolve_under_workspace(candidate_rel, field="review-candidate-snapshot")
                )
                comparison = build_comparison_result(
                    baseline,
                    candidate,
                    ack=RootAlignmentAck(
                        align_source_roots=align_source_roots,
                        align_database_roots=align_database_roots,
                    ),
                )
                write_comparison_artifact(comparison, comparison_abs)

            policy = None
            if drift_policy_rel:
                policy = load_drift_policy(
                    paths.resolve_under_workspace(drift_policy_rel, field="review-drift-policy")
                )
            drift_result_payload = build_drift_result(comparison, policy)
            drift_abs = output_abs / DRIFT_RESULT_NAME
            write_drift_artifact(drift_result_payload, drift_abs)
            drift_out_rel = f"{output_rel}/{DRIFT_RESULT_NAME}"
            drift_block = build_drift_block(
                comparison=comparison,
                drift_result=drift_result_payload,
            )

        review_result = build_review_result(conflicts=conflicts_block, drift=drift_block)
        try:
            validate_review_result(review_result)
        except ReviewResultError:
            return _materialize_failure("action_contract_invalid")

        review_abs = output_abs / REVIEW_RESULT_NAME
        write_review_result(review_result, review_abs)
        review_out_rel = f"{output_rel}/{REVIEW_RESULT_NAME}"

        conflicts = review_result["conflicts"]
        drift = review_result["drift"]
        conflict_summary = conflicts["summary"]
        drift_summary = drift.get("summary") if isinstance(drift.get("summary"), dict) else {}
        semantic_status = str(review_result["status"])
        state = ReviewRunState(
            status=semantic_status,
            review_status=semantic_status,
            conflict_status=str(conflicts["status"]),
            drift_status=str(drift["status"]),
            failure_code=None,
            review_result_path=review_out_rel,
            review_result_version=REVIEW_VERSION,
            comparison_result_path=comparison_out_rel,
            drift_result_path=drift_out_rel,
            conflict_property_count=str(int(conflict_summary.get("properties_analyzed", 0))),
            unresolved_conflict_count=str(int(conflict_summary.get("unresolved_conflict", 0))),
            resolved_authority_count=str(int(conflict_summary.get("resolved_by_authority", 0))),
            reconciliation_blocked_count=str(
                int(conflict_summary.get("reconciliation_blocked", 0))
            ),
            expected_difference_count=str(int(drift_summary.get("expected_differences", 0))),
            unexpected_drift_count=str(int(drift_summary.get("unexpected_drift", 0))),
            drift_affected_object_count=str(int(drift_summary.get("affected_objects", 0))),
            desired_exit_code=desired_review_exit_code(
                status=semantic_status,
                fail_on_review_blocked=fail_on_review_blocked,
            ),
            review_result=review_result,
            drift_result=drift_result_payload,
        )
        return _materialize_review_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            state=state,
            output_format=output_format,
            stdout=stdout,
        )
    except ReviewResultError as exc:
        codes = {item.code for item in exc.errors}
        if "write_error" in codes:
            return _materialize_failure("operational_failure")
        return _materialize_failure("action_contract_invalid")
    except ComparisonError as exc:
        if any(getattr(item, "code", "") == "write_error" for item in exc.errors):
            return _materialize_failure("operational_failure")
        return _materialize_failure("configuration_failed")
    except DriftError as exc:
        if any(getattr(item, "code", "") == "write_error" for item in exc.errors):
            return _materialize_failure("operational_failure")
        return _materialize_failure("configuration_failed")
    except (
        ObservationsArtifactError,
        ConfigContractError,
        ConfigResolutionError,
        AuthorityError,
        ComparisonArtifactError,
        SnapshotError,
    ):
        return _materialize_failure("configuration_failed")
    except OSError:
        return _materialize_failure("operational_failure")
    except ValueError:
        return _materialize_failure("action_contract_invalid")


def _config_result_path(output_rel: str) -> str:
    return f"{output_rel}/{CONFIG_RESULT_NAME}"


def _policy_result_path(output_rel: str) -> str:
    return f"{output_rel}/{POLICY_RESULT_NAME}"


def _plan_result_path(output_rel: str) -> str:
    return f"{output_rel}/{PLAN_RESULT_NAME}"


def _run_validate(
    *,
    paths: WorkspacePaths,
    config: str,
    profile: str,
    output_abs: Path,
    output_rel: str,
) -> tuple[int, dict[str, Any] | None, dict[str, Any]]:
    argv = ["config", "validate", "--config", config, "--json"]
    _append_profile(argv, profile)
    completed = run_governance_cli(argv, workspace=paths.workspace)
    try:
        payload = parse_cli_payload(completed.stdout, expect="config-diagnostics")
    except CliContractError:
        return completed.returncode, None, empty_validation()

    write_canonical_json(output_abs / CONFIG_RESULT_NAME, payload)
    rel = _config_result_path(output_rel)
    if completed.returncode == 0 and payload.get("ok") is True:
        validation = {"status": "passed", "cli_exit_code": 0, "result_path": rel}
        return 0, payload, validation
    validation = {
        "status": "failed",
        "cli_exit_code": completed.returncode if completed.returncode != 0 else 1,
        "result_path": rel,
    }
    return validation["cli_exit_code"], payload, validation


def _run_check(
    *,
    paths: WorkspacePaths,
    config: str,
    profile: str,
    output_abs: Path,
    output_rel: str,
) -> tuple[int, dict[str, Any] | None, dict[str, Any], str | None]:
    """Return exit, policy_report|diagnostic, policy stage, failure_code hint."""
    argv = ["check", "--config", config, "--format", "json"]
    _append_profile(argv, profile)
    completed = run_governance_cli(argv, workspace=paths.workspace)
    try:
        payload = parse_cli_payload(completed.stdout, expect="diagnostic-or-policy")
    except CliContractError:
        return completed.returncode, None, empty_policy(), "action_contract_invalid"

    if payload.get("report_schema") == "governance-policy-report":
        write_canonical_json(output_abs / POLICY_RESULT_NAME, payload)
        rel = _policy_result_path(output_rel)
        total, errors, warnings = count_policy_violations(payload)
        if completed.returncode == 0:
            policy = {
                "status": "passed",
                "cli_exit_code": 0,
                "violation_count": total,
                "error_count": errors,
                "warning_count": warnings,
                "result_path": rel,
            }
            return 0, payload, policy, None
        if completed.returncode == 3:
            policy = {
                "status": "blocked",
                "cli_exit_code": 3,
                "violation_count": total,
                "error_count": errors,
                "warning_count": warnings,
                "result_path": rel,
            }
            return 3, payload, policy, "policy_blocked"
        policy = {
            "status": "failed",
            "cli_exit_code": completed.returncode,
            "violation_count": total,
            "error_count": errors,
            "warning_count": warnings,
            "result_path": rel,
        }
        code = "configuration_failed" if completed.returncode == 4 else "operational_failure"
        return completed.returncode, payload, policy, code

    # Diagnostic family
    write_canonical_json(output_abs / POLICY_RESULT_NAME, payload)
    rel = _policy_result_path(output_rel)
    policy = {
        "status": "failed",
        "cli_exit_code": completed.returncode if completed.returncode != 0 else 1,
        "violation_count": 0,
        "error_count": 0,
        "warning_count": 0,
        "result_path": rel,
    }
    if completed.returncode == 4:
        return 4, payload, policy, "configuration_failed"
    if completed.returncode == 1:
        return 1, payload, policy, "operational_failure"
    return policy["cli_exit_code"], payload, policy, "action_contract_invalid"


def _run_plan(
    *,
    paths: WorkspacePaths,
    config: str,
    profile: str,
    plan_rel: str,
    output_abs: Path,
    output_rel: str,
) -> tuple[int, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any], str | None]:
    """Return exit, plan_dict, policy_report_if_exit3, plan stage, failure_code."""
    argv = ["plan", "--config", config, "--output", plan_rel, "--format", "json"]
    _append_profile(argv, profile)
    completed = run_governance_cli(argv, workspace=paths.workspace)
    try:
        payload = parse_cli_payload(completed.stdout, expect="plan-or-policy-or-diagnostic")
    except CliContractError:
        return completed.returncode, None, None, empty_plan(), "action_contract_invalid"

    if payload.get("report_schema") == "governance-policy-report":
        # Authoritative later policy report (plan exit 3).
        write_canonical_json(output_abs / POLICY_RESULT_NAME, payload)
        total, errors, warnings = count_policy_violations(payload)
        plan = {
            "status": "blocked",
            "cli_exit_code": 3,
            "create_count": 0,
            "update_count": 0,
            "unchanged_count": 0,
            "remote_only_count": 0,
            "plan_path": None,
            "result_path": None,
        }
        return 3, None, payload, plan, "policy_blocked"

    if payload.get("plan_schema") == "governance-plan":
        create, update, unchanged, remote_only = count_plan_actions(payload)
        if completed.returncode != 0:
            write_canonical_json(output_abs / PLAN_RESULT_NAME, payload)
            plan = {
                "status": "failed",
                "cli_exit_code": completed.returncode,
                "create_count": create,
                "update_count": update,
                "unchanged_count": unchanged,
                "remote_only_count": remote_only,
                "plan_path": None,
                "result_path": _plan_result_path(output_rel),
            }
            return completed.returncode, payload, None, plan, "action_contract_invalid"
        plan = {
            "status": "generated",
            "cli_exit_code": 0,
            "create_count": create,
            "update_count": update,
            "unchanged_count": unchanged,
            "remote_only_count": remote_only,
            "plan_path": plan_rel,
            "result_path": None,
        }
        return 0, payload, None, plan, None

    write_canonical_json(output_abs / PLAN_RESULT_NAME, payload)
    plan = {
        "status": "failed",
        "cli_exit_code": completed.returncode if completed.returncode != 0 else 1,
        "create_count": 0,
        "update_count": 0,
        "unchanged_count": 0,
        "remote_only_count": 0,
        "plan_path": None,
        "result_path": _plan_result_path(output_rel),
    }
    if completed.returncode == 4:
        return 4, None, None, plan, "configuration_failed"
    if completed.returncode == 1:
        return 1, None, None, plan, "operational_failure"
    return plan["cli_exit_code"], None, None, plan, "action_contract_invalid"


def run_orchestration(args: argparse.Namespace, *, stdout: TextIO | None = None) -> int:
    out = sys.stdout if stdout is None else stdout
    operation = ""
    impact_sources: list[tuple[str, str]] = []
    impact_changes_rel = ""
    impact_config_rel: str | None = None
    review_conflict_lane = False
    review_drift_lane = False
    review_observations_rel = ""
    review_comparison_rel = ""
    review_baseline_rel = ""
    review_candidate_rel = ""
    review_drift_policy_rel = ""
    review_config_rel: str | None = None
    review_align_source_roots = False
    review_align_database_roots = False
    fail_on_review_blocked = True
    try:
        operation = args.operation.strip()
        if operation not in {"validate", "check", "plan", "impact", "review"}:
            raise ValueError("operation must be validate, check, plan, impact, or review")
        output_format = args.output_format.strip()
        if output_format not in {"human", "json"}:
            raise ValueError("output-format must be human or json")
        fail_on = _parse_bool(args.fail_on_policy_error, field="fail-on-policy-error")
        _parse_bool(args.pr_comment, field="pr-comment")
        paths = WorkspacePaths.from_env()
        output_rel, output_abs = paths.validate_output_directory(args.output_directory)

        config = args.config if isinstance(args.config, str) else ""
        profile = args.profile or ""

        if operation in {"validate", "check", "plan"} and not config.strip():
            raise ActionInputContractError("config is required for validate, check, and plan")

        if operation == "impact":
            namespace = (args.impact_namespace or "").strip()
            if not namespace:
                raise ActionInputContractError("impact-namespace is required")
            changes_raw = args.impact_changes or ""
            if not str(changes_raw).strip():
                raise ActionInputContractError("impact-changes is required")
            impact_changes_rel = _validate_contained_path(
                paths, str(changes_raw), field="impact-changes"
            )
            impact_sources = _parse_impact_sources(
                paths,
                odcs_raw=args.impact_odcs or "",
                dbt_raw=args.impact_dbt_manifest or "",
                openlineage_raw=args.impact_openlineage or "",
            )
            if profile.strip() and not config.strip():
                raise ActionInputContractError("profile requires config for impact")
            if config.strip():
                impact_config_rel = _validate_contained_path(paths, config, field="config")

        if operation == "review":
            fail_on_review_blocked = _parse_bool(
                getattr(args, "fail_on_review_blocked", "true"),
                field="fail-on-review-blocked",
            )
            review_align_source_roots = _parse_bool(
                getattr(args, "review_align_source_roots", "false"),
                field="review-align-source-roots",
            )
            review_align_database_roots = _parse_bool(
                getattr(args, "review_align_database_roots", "false"),
                field="review-align-database-roots",
            )

            observations_raw = (getattr(args, "review_observations", None) or "").strip()
            comparison_raw = (getattr(args, "review_comparison", None) or "").strip()
            baseline_raw = (getattr(args, "review_baseline_snapshot", None) or "").strip()
            candidate_raw = (getattr(args, "review_candidate_snapshot", None) or "").strip()
            drift_policy_raw = (getattr(args, "review_drift_policy", None) or "").strip()

            review_conflict_lane = bool(observations_raw)
            has_comparison = bool(comparison_raw)
            has_baseline = bool(baseline_raw)
            has_candidate = bool(candidate_raw)

            if has_baseline ^ has_candidate:
                raise ActionInputContractError(
                    "review-baseline-snapshot and review-candidate-snapshot "
                    "must be supplied together"
                )
            if has_comparison and (has_baseline or has_candidate):
                raise ActionInputContractError(
                    "review-comparison is mutually exclusive with baseline/candidate snapshots"
                )
            if (review_align_source_roots or review_align_database_roots) and has_comparison:
                raise ActionInputContractError(
                    "review align flags require baseline/candidate snapshots, not review-comparison"
                )

            review_drift_lane = has_comparison ^ (has_baseline and has_candidate)
            if not review_conflict_lane and not review_drift_lane:
                raise ActionInputContractError(
                    "review requires review-observations and/or a drift lane "
                    "(review-comparison XOR review-baseline-snapshot+review-candidate-snapshot)"
                )
            if drift_policy_raw and not review_drift_lane:
                raise ActionInputContractError("review-drift-policy requires an enabled drift lane")
            if config.strip() and not review_conflict_lane:
                raise ActionInputContractError(
                    "config requires review-observations for operation=review"
                )
            if profile.strip() and not config.strip():
                raise ActionInputContractError("profile requires config for review")

            from governance.github_ci.review import (
                COMPARISON_RESULT_NAME,
                DRIFT_RESULT_NAME,
                REVIEW_RESULT_NAME,
            )

            forbidden_outputs = {
                (output_abs / name).resolve()
                for name in (
                    REVIEW_RESULT_NAME,
                    COMPARISON_RESULT_NAME,
                    DRIFT_RESULT_NAME,
                    "report.md",
                    "annotations.txt",
                )
            }
            input_abs_paths: list[Path] = []

            if review_conflict_lane:
                review_observations_rel, obs_abs = _validate_review_input_path(
                    paths, observations_raw, field="review-observations"
                )
                input_abs_paths.append(obs_abs)

            if has_comparison:
                review_comparison_rel, cmp_abs = _validate_review_input_path(
                    paths, comparison_raw, field="review-comparison"
                )
                input_abs_paths.append(cmp_abs)
            if has_baseline:
                review_baseline_rel, base_abs = _validate_review_input_path(
                    paths, baseline_raw, field="review-baseline-snapshot"
                )
                input_abs_paths.append(base_abs)
            if has_candidate:
                review_candidate_rel, cand_abs = _validate_review_input_path(
                    paths, candidate_raw, field="review-candidate-snapshot"
                )
                input_abs_paths.append(cand_abs)
            if drift_policy_raw:
                review_drift_policy_rel, pol_abs = _validate_review_input_path(
                    paths, drift_policy_raw, field="review-drift-policy"
                )
                input_abs_paths.append(pol_abs)
            if config.strip():
                _reject_path_controls(config, field="config")
                review_config_rel, cfg_abs = _validate_review_input_path(
                    paths, config, field="config"
                )
                input_abs_paths.append(cfg_abs)

            for input_abs in input_abs_paths:
                if input_abs.resolve() in forbidden_outputs:
                    raise ActionInputContractError(
                        "review input path collides with a fixed Action output artifact path"
                    )
    except (ValueError, PathValidationError, ActionInputContractError) as exc:
        message = _PHASE_A_STDERR
        if isinstance(exc, PathValidationError) and exc.code == "missing_workspace":
            message = "GITHUB_WORKSPACE is required"
        elif isinstance(exc, (ValueError, ActionInputContractError)):
            message = str(exc) or _PHASE_A_STDERR
        print(message, file=sys.stderr)
        write_github_output(_phase_a_outputs(operation=operation))
        return 0

    # Phase B: safe artifact root.
    output_abs.mkdir(parents=True, exist_ok=True)

    if operation == "impact":
        return _run_impact_operation(
            paths=paths,
            args=args,
            output_rel=output_rel,
            output_abs=output_abs,
            sources=impact_sources,
            changes_rel=impact_changes_rel,
            config_rel=impact_config_rel,
            output_format=output_format,
            stdout=out,
        )

    if operation == "review":
        return _run_review_operation(
            paths=paths,
            args=args,
            output_rel=output_rel,
            output_abs=output_abs,
            conflict_lane=review_conflict_lane,
            drift_lane=review_drift_lane,
            observations_rel=review_observations_rel,
            comparison_rel=review_comparison_rel,
            baseline_rel=review_baseline_rel,
            candidate_rel=review_candidate_rel,
            drift_policy_rel=review_drift_policy_rel,
            config_rel=review_config_rel,
            align_source_roots=review_align_source_roots,
            align_database_roots=review_align_database_roots,
            fail_on_review_blocked=fail_on_review_blocked,
            output_format=output_format,
            stdout=out,
        )

    plan_rel: str | None = None
    if operation == "plan":
        try:
            plan_rel, _plan_abs = paths.validate_plan_path(
                args.plan_path,
                output_directory_relative=output_rel,
                output_directory_absolute=output_abs,
            )
        except PathValidationError:
            action_result = build_action_result(
                operation=operation,
                status="failed",
                failure_code="action_contract_invalid",
                validation=empty_validation(),
            )
            return _materialize_phase_b(
                output_rel=output_rel,
                output_abs=output_abs,
                action_result=action_result,
                policy_report=None,
                plan_dict=None,
                fail_on_policy_error=fail_on,
                output_format=output_format,
                stdout=out,
            )

    config = args.config
    profile = args.profile or ""

    try:
        config_rel = paths.normalize_relative(config, field="config")
        paths.resolve_under_workspace(config_rel, field="config")
    except PathValidationError:
        action_result = build_action_result(
            operation=operation,
            status="failed",
            failure_code="action_contract_invalid",
            validation=empty_validation(),
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=None,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    # Always validate first for all operations.
    try:
        _v_code, config_payload, validation = _run_validate(
            paths=paths,
            config=config,
            profile=profile,
            output_abs=output_abs,
            output_rel=output_rel,
        )
    except CliContractError:
        action_result = build_action_result(
            operation=operation,
            status="failed",
            failure_code="action_contract_invalid",
            validation=empty_validation(),
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=None,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    if config_payload is None:
        action_result = build_action_result(
            operation=operation,
            status="failed",
            failure_code="action_contract_invalid",
            validation=empty_validation(),
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=None,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    if validation["status"] != "passed":
        action_result = build_action_result(
            operation=operation,
            status="failed",
            failure_code="configuration_failed",
            validation=validation,
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=None,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    if operation == "validate":
        action_result = build_action_result(
            operation=operation,
            status="passed",
            failure_code=None,
            validation=validation,
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=None,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    try:
        _c_code, check_payload, policy, policy_fail = _run_check(
            paths=paths,
            config=config,
            profile=profile,
            output_abs=output_abs,
            output_rel=output_rel,
        )
    except CliContractError:
        action_result = build_action_result(
            operation=operation,
            status="failed",
            failure_code="action_contract_invalid",
            validation=validation,
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=None,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    policy_report = (
        check_payload
        if check_payload is not None
        and check_payload.get("report_schema") == "governance-policy-report"
        else None
    )

    if policy_fail == "action_contract_invalid" or check_payload is None:
        action_result = build_action_result(
            operation=operation,
            status="failed",
            failure_code="action_contract_invalid",
            validation=validation,
            policy=policy,
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=policy_report,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    if policy["status"] == "blocked":
        plan_stage = empty_plan()
        if operation == "plan":
            plan_stage = {**empty_plan(), "status": "blocked"}
        action_result = build_action_result(
            operation=operation,
            status="blocked",
            failure_code="policy_blocked",
            validation=validation,
            policy=policy,
            plan=plan_stage,
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=policy_report,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    if policy["status"] == "failed":
        plan_stage = empty_plan()
        if operation == "plan":
            plan_stage = {**empty_plan(), "status": "failed"}
        action_result = build_action_result(
            operation=operation,
            status="failed",
            failure_code=policy_fail or "operational_failure",
            validation=validation,
            policy=policy,
            plan=plan_stage,
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=policy_report,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    if operation == "check":
        action_result = build_action_result(
            operation=operation,
            status="passed",
            failure_code=None,
            validation=validation,
            policy=policy,
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=policy_report,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    assert plan_rel is not None
    assert config_payload is not None
    assert policy_report is not None

    try:
        _p_code, plan_dict, later_policy, plan_stage, plan_fail = _run_plan(
            paths=paths,
            config=config,
            profile=profile,
            plan_rel=plan_rel,
            output_abs=output_abs,
            output_rel=output_rel,
        )
    except CliContractError:
        action_result = build_action_result(
            operation=operation,
            status="failed",
            failure_code="action_contract_invalid",
            validation=validation,
            policy=policy,
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=policy_report,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    if later_policy is not None:
        total, errors, warnings = count_policy_violations(later_policy)
        policy = {
            "status": "blocked",
            "cli_exit_code": 3,
            "violation_count": total,
            "error_count": errors,
            "warning_count": warnings,
            "result_path": _policy_result_path(output_rel),
        }
        action_result = build_action_result(
            operation=operation,
            status="blocked",
            failure_code="policy_blocked",
            validation=validation,
            policy=policy,
            plan=plan_stage,
            consistency_status="not_applicable",
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=later_policy,
            plan_dict=None,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    if plan_stage["status"] != "generated" or plan_dict is None:
        action_result = build_action_result(
            operation=operation,
            status="failed",
            failure_code=plan_fail or "operational_failure",
            validation=validation,
            policy=policy,
            plan=plan_stage,
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=policy_report,
            plan_dict=plan_dict,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    if not check_plan_identity_consistency(
        config_diagnostics=config_payload,
        policy_report=policy_report,
        plan_dict=plan_dict,
    ):
        plan_stage = {**plan_stage, "status": "failed", "plan_path": None}
        action_result = build_action_result(
            operation=operation,
            status="failed",
            failure_code="inputs_changed_during_run",
            validation=validation,
            policy=policy,
            plan=plan_stage,
            consistency_status="failed",
        )
        return _materialize_phase_b(
            output_rel=output_rel,
            output_abs=output_abs,
            action_result=action_result,
            policy_report=policy_report,
            plan_dict=plan_dict,
            fail_on_policy_error=fail_on,
            output_format=output_format,
            stdout=out,
        )

    action_result = build_action_result(
        operation=operation,
        status="passed",
        failure_code=None,
        validation=validation,
        policy=policy,
        plan=plan_stage,
        consistency_status="passed",
    )
    return _materialize_phase_b(
        output_rel=output_rel,
        output_abs=output_abs,
        action_result=action_result,
        policy_report=policy_report,
        plan_dict=plan_dict,
        fail_on_policy_error=fail_on,
        output_format=output_format,
        stdout=out,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="governance.github_ci")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run governance Action orchestration")
    run.add_argument("--config", default="")
    run.add_argument("--profile", default="")
    run.add_argument("--operation", required=True)
    run.add_argument("--output-format", required=True)
    run.add_argument("--fail-on-policy-error", required=True)
    run.add_argument("--fail-on-review-blocked", default="true")
    run.add_argument("--output-directory", required=True)
    run.add_argument("--plan-path", required=True)
    run.add_argument("--pr-comment", required=True)
    run.add_argument("--impact-namespace", default="")
    run.add_argument("--impact-changes", default="")
    run.add_argument("--impact-odcs", default="")
    run.add_argument("--impact-dbt-manifest", default="")
    run.add_argument("--impact-openlineage", default="")
    run.add_argument("--dbt-default-database", default="")
    run.add_argument("--review-observations", default="")
    run.add_argument("--review-comparison", default="")
    run.add_argument("--review-baseline-snapshot", default="")
    run.add_argument("--review-candidate-snapshot", default="")
    run.add_argument("--review-drift-policy", default="")
    run.add_argument("--review-align-source-roots", default="false")
    run.add_argument("--review-align-database-roots", default="false")

    sub.add_parser(
        "emit-annotations-and-comment-state",
        help="Emit annotations and compute comment eligibility",
    )
    sub.add_parser("comment", help="Publish sticky PR comment")
    sub.add_parser("finalize", help="Aggregate final Action outputs")
    sub.add_parser("fail-gate", help="Exit with final-exit-code")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "run":
        return run_orchestration(args)
    if args.command == "emit-annotations-and-comment-state":
        from governance.github_ci.finalize import emit_annotations_and_comment_state

        return emit_annotations_and_comment_state()
    if args.command == "comment":
        from governance.github_ci.comment import main as comment_main

        return comment_main()
    if args.command == "finalize":
        from governance.github_ci.finalize import finalize_outputs

        return finalize_outputs()
    if args.command == "fail-gate":
        from governance.github_ci.finalize import fail_gate

        return fail_gate()
    parser.error(f"unknown command: {args.command}")
    return 2
