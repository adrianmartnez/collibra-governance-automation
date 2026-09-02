"""Deterministic Markdown reports and workflow annotation preparation."""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any

from governance.github_ci.result import ANNOTATIONS_NAME, REPORT_NAME
from governance.impact import format_human_value
from governance.io.atomic import atomic_write_text

MAX_MARKDOWN_VIOLATIONS = 20
MAX_MARKDOWN_PLAN_ACTIONS = 20
MAX_DISPLAY_VALUE_CHARS = 200
MAX_ANNOTATIONS = 20
MAX_IMPACT_LIST_ITEMS = 20
MAX_IMPACT_PATHS = 10

_STATUS_OVERALL = {
    "passed": "PASS",
    "blocked": "BLOCKED",
    "failed": "FAILED",
}
_STATUS_VALIDATION = {
    "not_run": "NOT RUN",
    "passed": "PASS",
    "failed": "FAILED",
}
_STATUS_POLICY = {
    "not_run": "NOT RUN",
    "passed": "PASS",
    "blocked": "BLOCKED",
    "failed": "FAILED",
}
_STATUS_PLAN = {
    "not_run": "NOT RUN",
    "generated": "GENERATED",
    "blocked": "BLOCKED",
    "failed": "FAILED",
}


def normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def truncate_display(value: str, *, limit: int = MAX_DISPLAY_VALUE_CHARS) -> str:
    text = normalize_newlines(value)
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def escape_markdown(value: str) -> str:
    """Escape untrusted strings for Markdown report bodies."""
    text = truncate_display(normalize_newlines(value))
    text = text.replace("`", "\\`")
    text = text.replace("|", "\\|")
    text = html.escape(text, quote=False)
    return text


def escape_workflow_command(value: str) -> str:
    """Escape values embedded in GitHub Actions workflow commands."""
    text = normalize_newlines(value)
    text = text.replace("%", "%25")
    text = text.replace("\r", "%0D")
    text = text.replace("\n", "%0A")
    return text


def _violation_line(item: dict[str, Any]) -> str:
    severity = str(item.get("severity", ""))
    policy_id = str(item.get("policy_id", ""))
    kind = str(item.get("object_kind", ""))
    object_id = str(item.get("object_id", ""))
    reason = str(item.get("reason", ""))
    name = item.get("object_name")
    name_part = f" name={name}" if name else ""
    return f"{severity} policy={policy_id} kind={kind} id={object_id}{name_part} reason={reason}"


def _plan_action_lines(plan_dict: dict[str, Any]) -> list[str]:
    actions = plan_dict.get("actions")
    if not isinstance(actions, list):
        return []
    lines: list[str] = []
    for item in actions:
        if not isinstance(item, dict):
            continue
        action_type = item.get("action_type")
        if action_type not in {"create", "update"}:
            continue
        local_id = str(item.get("local_id") or "")
        lines.append(f"{str(action_type).upper()} {local_id}".rstrip())
    return lines


def render_report(
    action_result: dict[str, Any],
    policy_report: dict[str, Any] | None = None,
    plan_dict: dict[str, Any] | None = None,
    *,
    artifacts_relative: str | None = None,
) -> str:
    validation = action_result["validation"]
    policy = action_result["policy"]
    plan = action_result["plan"]
    root = (artifacts_relative or "").rstrip("/")

    def under_root(name: str) -> str:
        return f"{root}/{name}" if root else name

    lines = [
        "# Governance as Code",
        "",
        "## Status",
        f"OVERALL: {_STATUS_OVERALL[action_result['status']]}",
        f"CONFIGURATION: {_STATUS_VALIDATION[validation['status']]}",
        f"POLICY: {_STATUS_POLICY[policy['status']]}",
        f"PLAN: {_STATUS_PLAN[plan['status']]}",
        "EXECUTION: NOT REQUESTED",
        "Writes performed: 0",
        "",
        "## Policy",
        (
            f"Violations: {policy['violation_count']} "
            f"(errors={policy['error_count']}, warnings={policy['warning_count']})"
        ),
    ]

    if policy_report is not None:
        violations = policy_report.get("violations")
        if isinstance(violations, list) and violations:
            shown = violations[:MAX_MARKDOWN_VIOLATIONS]
            for item in shown:
                if isinstance(item, dict):
                    lines.append(f"- {escape_markdown(_violation_line(item))}")
            omitted = len(violations) - len(shown)
            if omitted > 0:
                ref = policy.get("result_path") or under_root("policy-result.json")
                lines.append(f"- … omitted {omitted} more; see {escape_markdown(str(ref))}")

    lines.extend(
        [
            "",
            "## Plan",
            f"Create: {plan['create_count']}",
            f"Update: {plan['update_count']}",
            f"Unchanged: {plan['unchanged_count']}",
            f"Remote only: {plan['remote_only_count']}",
        ]
    )

    if plan_dict is not None and plan["status"] == "generated":
        action_lines = _plan_action_lines(plan_dict)
        shown_actions = action_lines[:MAX_MARKDOWN_PLAN_ACTIONS]
        for entry in shown_actions:
            lines.append(f"- {escape_markdown(entry)}")
        omitted_actions = len(action_lines) - len(shown_actions)
        if omitted_actions > 0:
            lines.append(f"- … omitted {omitted_actions} more CREATE/UPDATE actions")

    lines.extend(["", "## Artifacts"])
    action_result_path = under_root("action-result.json")
    lines.append(f"- action-result: {escape_markdown(action_result_path)}")
    if policy.get("result_path"):
        lines.append(f"- policy-result: {escape_markdown(str(policy['result_path']))}")
    if plan.get("plan_path"):
        lines.append(f"- plan: {escape_markdown(str(plan['plan_path']))}")
    elif plan.get("result_path"):
        lines.append(f"- plan: {escape_markdown(str(plan['result_path']))}")

    text = "\n".join(lines) + "\n"
    # Safety: never emit the forbidden apply wording.
    return re.sub(r"(?i)\bapplied\b", "not-requested", text)


def render_human_summary(action_result: dict[str, Any]) -> str:
    validation = action_result["validation"]
    policy = action_result["policy"]
    plan = action_result["plan"]
    lines = [
        f"OVERALL: {_STATUS_OVERALL[action_result['status']]}",
        f"CONFIGURATION: {_STATUS_VALIDATION[validation['status']]}",
        f"POLICY: {_STATUS_POLICY[policy['status']]}",
        f"PLAN: {_STATUS_PLAN[plan['status']]}",
        "Writes performed: 0",
        (
            f"Violations: {policy['violation_count']} "
            f"(errors={policy['error_count']}, warnings={policy['warning_count']})"
        ),
        (
            f"Create: {plan['create_count']} "
            f"Update: {plan['update_count']} "
            f"Unchanged: {plan['unchanged_count']} "
            f"Remote only: {plan['remote_only_count']}"
        ),
    ]
    if action_result.get("failure_code"):
        lines.append(f"Failure: {action_result['failure_code']}")
    return "\n".join(lines) + "\n"


def build_annotations(
    action_result: dict[str, Any],
    policy_report: dict[str, Any] | None = None,
) -> list[str]:
    hard: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []

    if action_result["status"] == "failed":
        code = action_result.get("failure_code") or "action_contract_invalid"
        hard.append(f"::error::{escape_workflow_command(f'governance action failed: {code}')}")

    if policy_report is not None:
        violations = policy_report.get("violations")
        if isinstance(violations, list):
            for item in violations:
                if not isinstance(item, dict):
                    continue
                message = escape_workflow_command(_violation_line(item))
                if item.get("severity") == "error":
                    errors.append(f"::error::{message}")
                elif item.get("severity") == "warning":
                    warnings.append(f"::warning::{message}")

    candidates = hard + errors + warnings
    if len(candidates) <= MAX_ANNOTATIONS:
        return candidates

    kept = candidates[: MAX_ANNOTATIONS - 1]
    omitted = len(candidates) - len(kept)
    kept.append(
        "::warning::"
        + escape_workflow_command(
            f"governance annotations truncated; omitted {omitted} additional items"
        )
    )
    return kept


def write_annotations_file(output_directory: Path, annotations: list[str]) -> Path:
    target = output_directory / ANNOTATIONS_NAME
    payload = "\n".join(annotations)
    if annotations:
        payload += "\n"
    else:
        payload = ""
    return atomic_write_text(target, payload)


def write_report_file(output_directory: Path, report_text: str) -> Path:
    return atomic_write_text(output_directory / REPORT_NAME, report_text)


def _impact_safe_display(value: str) -> str:
    """Line-safe, truncated, Markdown-escaped display for impact identifiers."""
    return escape_markdown(truncate_display(format_human_value(value)))


def _node_label(node: dict[str, Any]) -> str:
    kind = str(node.get("kind", ""))
    logical_id = str(node.get("logical_id", ""))
    return f"{kind}/{logical_id}"


def _bounded_list_section(
    title: str,
    items: list[str],
    *,
    limit: int,
    impact_result_path: str,
) -> list[str]:
    lines = ["", f"## {title}"]
    if not items:
        lines.append("(none)")
        return lines
    shown = items[:limit]
    for entry in shown:
        lines.append(f"- {_impact_safe_display(entry)}")
    total = len(items)
    if total > limit:
        ref = impact_result_path or "impact-result.json"
        lines.append(f"- showing {limit} of {total}; see {_impact_safe_display(ref)}")
    return lines


def _identity_lines(nodes: Any) -> list[str]:
    if not isinstance(nodes, list):
        return []
    lines: list[str] = []
    for node in nodes:
        if isinstance(node, dict):
            lines.append(_node_label(node))
    return lines


def _policy_lines(policies: Any) -> list[str]:
    if not isinstance(policies, list):
        return []
    lines: list[str] = []
    for item in policies:
        if not isinstance(item, dict):
            continue
        policy_id = str(item.get("policy_id", ""))
        severity = str(item.get("severity", ""))
        rule = str(item.get("rule_type", ""))
        matches = item.get("matched_objects")
        match_count = len(matches) if isinstance(matches, list) else 0
        lines.append(f"id={policy_id} severity={severity} rule={rule} matches={match_count}")
    return lines


def _path_lines(paths: Any) -> list[str]:
    if not isinstance(paths, list):
        return []
    lines: list[str] = []
    for item in paths:
        if not isinstance(item, dict):
            continue
        root = item.get("root") if isinstance(item.get("root"), dict) else {}
        target = item.get("target") if isinstance(item.get("target"), dict) else {}
        distance = item.get("distance", "")
        steps = item.get("steps")
        step_bits: list[str] = []
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                traversal = str(step.get("traversal", ""))
                edge = step.get("edge") if isinstance(step.get("edge"), dict) else {}
                edge_kind = str(edge.get("kind", ""))
                step_bits.append(f"{traversal}:{edge_kind}")
        lines.append(
            f"root={_node_label(root)} target={_node_label(target)} "
            f"distance={distance} steps={','.join(step_bits)}"
        )
    return lines


def render_impact_report(
    *,
    impact_status: str,
    impact_result: dict[str, Any] | None,
    impact_result_path: str | None,
    failure_code: str | None = None,
    diagnostic: dict[str, Any] | None = None,
    artifacts_relative: str | None = None,
) -> str:
    """Deterministic Markdown report for operation=impact (legacy report untouched)."""
    root = (artifacts_relative or "").rstrip("/")

    def under_root(name: str) -> str:
        return f"{root}/{name}" if root else name

    status_label = {
        "clear": "CLEAR",
        "impacted": "IMPACTED",
        "failed": "FAILED",
        "not_run": "NOT RUN",
    }.get(impact_status, impact_status.upper())

    impact_payload = impact_result.get("impact") if isinstance(impact_result, dict) else None
    if not isinstance(impact_payload, dict):
        impact_payload = {}

    changed = _identity_lines(impact_payload.get("changed_nodes"))
    direct = _identity_lines(impact_payload.get("direct_nodes"))
    transitive = _identity_lines(impact_payload.get("transitive_nodes"))
    assets = _identity_lines(impact_payload.get("governance_assets"))
    contracts = _identity_lines(impact_payload.get("associated_contracts"))
    policies = _policy_lines(
        impact_result.get("affected_policies") if isinstance(impact_result, dict) else []
    )
    paths = _path_lines(impact_payload.get("paths"))
    result_ref = impact_result_path or under_root("impact-result.json")

    lines = [
        "# Governance as Code — Impact",
        "",
        "## Status",
        f"IMPACT: {status_label}",
        "EXECUTION: NOT REQUESTED",
        "Writes performed: 0",
    ]
    if failure_code:
        lines.append(f"Failure: {_impact_safe_display(failure_code)}")

    if impact_status in {"clear", "impacted"}:
        lines.extend(
            [
                "",
                "## Counts",
                f"Changed: {len(changed)}",
                f"Direct: {len(direct)}",
                f"Transitive: {len(transitive)}",
                f"Contracts: {len(contracts)}",
                f"Governance assets: {len(assets)}",
                f"Affected policies: {len(policies)}",
            ]
        )
        lines.extend(
            _bounded_list_section(
                "Changed roots",
                changed,
                limit=MAX_IMPACT_LIST_ITEMS,
                impact_result_path=result_ref,
            )
        )
        lines.extend(
            _bounded_list_section(
                "Direct nodes",
                direct,
                limit=MAX_IMPACT_LIST_ITEMS,
                impact_result_path=result_ref,
            )
        )
        lines.extend(
            _bounded_list_section(
                "Transitive nodes",
                transitive,
                limit=MAX_IMPACT_LIST_ITEMS,
                impact_result_path=result_ref,
            )
        )
        lines.extend(
            _bounded_list_section(
                "Governance assets",
                assets,
                limit=MAX_IMPACT_LIST_ITEMS,
                impact_result_path=result_ref,
            )
        )
        lines.extend(
            _bounded_list_section(
                "Associated contracts",
                contracts,
                limit=MAX_IMPACT_LIST_ITEMS,
                impact_result_path=result_ref,
            )
        )
        lines.extend(
            _bounded_list_section(
                "Affected policies",
                policies,
                limit=MAX_IMPACT_LIST_ITEMS,
                impact_result_path=result_ref,
            )
        )
        lines.extend(
            _bounded_list_section(
                "Explainable paths",
                paths,
                limit=MAX_IMPACT_PATHS,
                impact_result_path=result_ref,
            )
        )
    elif diagnostic is not None:
        errors = diagnostic.get("errors")
        lines.extend(["", "## Diagnostics"])
        if isinstance(errors, list) and errors:
            shown = errors[:MAX_IMPACT_LIST_ITEMS]
            for item in shown:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code", ""))
                message = str(item.get("message", ""))
                path = str(item.get("path", ""))
                lines.append(
                    f"- code={_impact_safe_display(code)} "
                    f"path={_impact_safe_display(path)} "
                    f"message={_impact_safe_display(message)}"
                )
            omitted = len(errors) - len(shown)
            if omitted > 0:
                lines.append(f"- showing {len(shown)} of {len(errors)}; see diagnostics")
        else:
            lines.append("- impact analysis failed")

    lines.extend(["", "## Artifacts"])
    if impact_result_path:
        lines.append(f"- impact-result: {_impact_safe_display(impact_result_path)}")
    else:
        lines.append("- impact-result: (none)")

    text = "\n".join(lines) + "\n"
    return re.sub(r"(?i)\bapplied\b", "not-requested", text)


def render_impact_human_summary(
    *,
    impact_status: str,
    impact_result: dict[str, Any] | None,
    impact_result_path: str | None,
    failure_code: str | None = None,
) -> str:
    impact_payload = impact_result.get("impact") if isinstance(impact_result, dict) else None
    if not isinstance(impact_payload, dict):
        impact_payload = {}
    policies = impact_result.get("affected_policies") if isinstance(impact_result, dict) else []
    policy_count = len(policies) if isinstance(policies, list) else 0
    lines = [
        f"IMPACT: {impact_status.upper()}",
        "Writes performed: 0",
        (
            f"Changed: {len(_identity_lines(impact_payload.get('changed_nodes')))} "
            f"Direct: {len(_identity_lines(impact_payload.get('direct_nodes')))} "
            f"Transitive: {len(_identity_lines(impact_payload.get('transitive_nodes')))} "
            f"Policies: {policy_count}"
        ),
    ]
    if impact_result_path:
        lines.append(f"Artifact: {format_human_value(impact_result_path)}")
    if failure_code:
        lines.append(f"Failure: {format_human_value(failure_code)}")
    return "\n".join(lines) + "\n"


def build_impact_annotations(
    *,
    impact_status: str,
    impact_result: dict[str, Any] | None = None,
    failure_code: str | None = None,
) -> list[str]:
    if impact_status == "clear":
        return []
    if impact_status == "impacted":
        impact_payload = impact_result.get("impact") if isinstance(impact_result, dict) else None
        if not isinstance(impact_payload, dict):
            impact_payload = {}
        direct = len(_identity_lines(impact_payload.get("direct_nodes")))
        transitive = len(_identity_lines(impact_payload.get("transitive_nodes")))
        policies = impact_result.get("affected_policies") if isinstance(impact_result, dict) else []
        policy_count = len(policies) if isinstance(policies, list) else 0
        message = (
            f"governance impact: impacted; direct={direct} "
            f"transitive={transitive} policies={policy_count}"
        )
        return [f"::warning::{escape_workflow_command(format_human_value(message))}"]
    code = failure_code or "action_contract_invalid"
    message = f"governance impact failed: {code}"
    return [f"::error::{escape_workflow_command(format_human_value(message))}"]


MAX_REVIEW_CONFLICT_ITEMS = 20
MAX_REVIEW_DRIFT_ITEMS = 20

_REVIEW_STATUS = {
    "passed": "PASS",
    "blocked": "BLOCKED",
    "failed": "FAILED",
}
_CONFLICT_STATUS = {
    "not_run": "NOT RUN",
    "clear": "CLEAR",
    "findings": "FINDINGS",
    "blocked": "BLOCKED",
}
_DRIFT_STATUS = {
    "not_run": "NOT RUN",
    "no_difference": "NO DIFFERENCE",
    "expected_difference": "EXPECTED DIFFERENCE",
    "unexpected_drift": "UNEXPECTED DRIFT",
    "failed": "FAILED",
}


def _digest_display(identity: dict[str, Any] | None) -> str:
    if not isinstance(identity, dict):
        return "(none)"
    digest = identity.get("digest")
    if not isinstance(digest, str) or not digest:
        return "(none)"
    return _review_safe_scalar(digest)


def _review_safe_scalar(value: object) -> str:
    """Single-line safe scalar for review Markdown (controls visible as escapes)."""
    text = json.dumps(str(value), ensure_ascii=True)[1:-1]
    return escape_markdown(text)


def _compact_object_identity(obj: dict[str, Any]) -> str:
    """Deterministic compact GraphNodeIdentity display including parent chain."""
    parts: list[str] = []
    current: dict[str, Any] | None = obj
    while isinstance(current, dict):
        parts.append(
            f"{current.get('namespace', '')}/{current.get('kind', '')}/"
            f"{current.get('logical_id', '')}"
        )
        parent = current.get("parent")
        current = parent if isinstance(parent, dict) else None
    # Join with plain ASCII (no HTML-special chars); child first, then parents.
    return " under ".join(parts)


def _object_identity_digest(obj: dict[str, Any]) -> str:
    from governance.identity.canonicalize import canonical_json_bytes

    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def _review_conflict_item_lines(
    review_result: dict[str, Any],
) -> list[tuple[int, str]]:
    """Return (priority, line) for notable conflict items. Lower priority first."""
    from governance.identity.canonicalize import canonical_json_bytes

    conflicts = review_result.get("conflicts")
    if not isinstance(conflicts, dict):
        return []
    report = conflicts.get("conflict_report")
    assessments = conflicts.get("reconciliation_assessments")
    if not isinstance(report, dict) or not isinstance(assessments, list):
        return []
    results = report.get("results")
    if not isinstance(results, list):
        return []
    if len(results) != len(assessments):
        return []

    notable: list[tuple[int, bytes, str, str]] = []
    for result, assessment in zip(results, assessments, strict=True):
        if not isinstance(result, dict) or not isinstance(assessment, dict):
            continue
        state = str(result.get("state", ""))
        if state not in {
            "RESOLVED_BY_AUTHORITY",
            "UNRESOLVED_CONFLICT",
            "INVALID_OR_AMBIGUOUS_AUTHORITY",
        }:
            continue
        obj = result.get("object")
        if not isinstance(obj, dict):
            continue
        prop = str(result.get("property", ""))
        kind = str(obj.get("kind", ""))
        logical_id = str(obj.get("logical_id", ""))
        reason = str(result.get("reason", ""))
        blocks = bool(assessment.get("applicable")) and not bool(assessment.get("safe", True))
        authority_resolved = state == "RESOLVED_BY_AUTHORITY"
        priority = {
            "INVALID_OR_AMBIGUOUS_AUTHORITY": 0,
            "UNRESOLVED_CONFLICT": 1,
            "RESOLVED_BY_AUTHORITY": 2,
        }[state]
        sort_key = canonical_json_bytes(obj)
        parts = [
            f"kind={_review_safe_scalar(kind)}",
            f"logical_id={_review_safe_scalar(logical_id)}",
            f"property={_review_safe_scalar(prop)}",
            f"state={_review_safe_scalar(state)}",
            f"reason={_review_safe_scalar(reason)}",
            f"object_identity={_review_safe_scalar(_compact_object_identity(obj))}",
            f"object_identity_digest={_review_safe_scalar(_object_identity_digest(obj))}",
            f"blocks_reconciliation={'true' if blocks else 'false'}",
        ]
        if authority_resolved:
            parts.append("authority_resolved=true")
        notable.append(
            (priority, sort_key + b"\0" + prop.encode("utf-8"), prop, "- " + " ".join(parts))
        )

    notable.sort(key=lambda item: (item[0], item[1], item[2]))
    return [(item[0], item[3]) for item in notable]


def _review_drift_item_lines(drift_result: dict[str, Any] | None) -> list[str]:
    if not isinstance(drift_result, dict):
        return []
    from governance.identity.canonicalize import canonical_json_bytes

    changes = drift_result.get("classified_changes")
    if not isinstance(changes, list):
        return []
    unexpected: list[tuple[bytes, str]] = []
    for item in changes:
        if not isinstance(item, dict):
            continue
        if item.get("classification") != "unexpected_drift":
            continue
        obj = item.get("object_identity")
        if not isinstance(obj, dict):
            continue
        change = str(item.get("change", ""))
        reason = str(item.get("reason", ""))
        kind = str(obj.get("kind", ""))
        path_parts = obj.get("path")
        path_text = (
            "/".join(str(part) for part in path_parts) if isinstance(path_parts, list) else ""
        )
        prop = item.get("property")
        prop_text = str(prop) if isinstance(prop, str) and prop else ""
        sort_key = canonical_json_bytes(obj) + b"\0" + prop_text.encode("utf-8")
        parts = [
            f"change={_review_safe_scalar(change)}",
            f"kind={_review_safe_scalar(kind)}",
            f"path={_review_safe_scalar(path_text)}",
        ]
        if prop_text:
            parts.append(f"property={_review_safe_scalar(prop_text)}")
        parts.append(f"reason={_review_safe_scalar(reason)}")
        unexpected.append((sort_key, "- " + " ".join(parts)))
    unexpected.sort(key=lambda item: item[0])
    return [line for _, line in unexpected]


def render_review_report(
    *,
    review_status: str,
    review_result: dict[str, Any] | None,
    drift_result: dict[str, Any] | None = None,
    failure_code: str | None = None,
    artifacts_relative: str | None = None,
    review_result_path: str | None = None,
    comparison_result_path: str | None = None,
    drift_result_path: str | None = None,
    conflict_status: str = "not_run",
    drift_status: str = "not_run",
) -> str:
    """Deterministic Markdown report for operation=review (legacy report untouched)."""
    root = (artifacts_relative or "").rstrip("/")

    def under_root(name: str) -> str:
        return f"{root}/{name}" if root else name

    review_label = _REVIEW_STATUS.get(review_status, review_status.upper())
    conflict_label = _CONFLICT_STATUS.get(conflict_status, conflict_status.upper())
    report_drift_status = drift_status
    if review_status == "failed" and drift_status == "not_run" and failure_code:
        report_drift_status = "failed"
    drift_label = _DRIFT_STATUS.get(report_drift_status, report_drift_status.upper())

    lines = [
        "# Governance as Code — Review",
        "",
        "## Status",
        f"REVIEW: {review_label}",
        f"CONFLICTS: {conflict_label}",
        f"DRIFT: {drift_label}",
        "EXECUTION: NOT REQUESTED",
        "Writes performed: 0",
    ]
    if failure_code:
        lines.append(f"Failure: {_review_safe_scalar(failure_code)}")

    conflicts = review_result.get("conflicts") if isinstance(review_result, dict) else None
    if isinstance(conflicts, dict) and conflicts.get("status") != "not_run":
        summary = conflicts.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        lines.extend(
            [
                "",
                "## Authority & conflicts",
                f"Properties analyzed: {int(summary.get('properties_analyzed', 0))}",
                f"Resolved by authority: {int(summary.get('resolved_by_authority', 0))}",
                f"Unresolved conflicts: {int(summary.get('unresolved_conflict', 0))}",
                (
                    "Invalid / ambiguous authority: "
                    f"{int(summary.get('invalid_or_ambiguous_authority', 0))}"
                ),
                f"Reconciliation blocked: {int(summary.get('reconciliation_blocked', 0))}",
            ]
        )
        items = _review_conflict_item_lines(review_result or {})
        shown = items[:MAX_REVIEW_CONFLICT_ITEMS]
        if shown:
            lines.append("")
            lines.append("Notable conflict findings:")
            lines.extend(line for _, line in shown)
        omitted = len(items) - len(shown)
        if omitted > 0:
            ref = review_result_path or under_root("review-result.json")
            lines.append(f"showing {len(shown)} of {len(items)}; see {_review_safe_scalar(ref)}")

    drift_block = review_result.get("drift") if isinstance(review_result, dict) else None
    if isinstance(drift_block, dict) and drift_block.get("status") != "not_run":
        summary = drift_block.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        lines.extend(
            [
                "",
                "## Drift",
                f"Baseline snapshot: "
                f"{_digest_display(drift_block.get('baseline_snapshot_identity'))}",
                f"Candidate snapshot: "
                f"{_digest_display(drift_block.get('candidate_snapshot_identity'))}",
                f"Status: {_review_safe_scalar(str(drift_block.get('status', '')))}",
                f"Expected differences: {int(summary.get('expected_differences', 0))}",
                f"Unexpected drift: {int(summary.get('unexpected_drift', 0))}",
                f"Affected objects: {int(summary.get('affected_objects', 0))}",
                f"Property drift: {int(summary.get('property_drift', 0))}",
            ]
        )
        drift_items = _review_drift_item_lines(drift_result)
        shown_drift = drift_items[:MAX_REVIEW_DRIFT_ITEMS]
        if shown_drift:
            lines.append("")
            lines.append("Unexpected drift findings:")
            lines.extend(shown_drift)
        omitted_drift = len(drift_items) - len(shown_drift)
        if omitted_drift > 0:
            ref = drift_result_path or under_root("drift-result.json")
            lines.append(
                f"showing {len(shown_drift)} of {len(drift_items)}; see {_review_safe_scalar(ref)}"
            )

    lines.extend(["", "## Artifacts"])
    if review_result_path:
        lines.append(f"- review-result: {_review_safe_scalar(review_result_path)}")
    else:
        lines.append("- review-result: (none)")
    if comparison_result_path:
        lines.append(f"- comparison-result: {_review_safe_scalar(comparison_result_path)}")
    if drift_result_path:
        lines.append(f"- drift-result: {_review_safe_scalar(drift_result_path)}")

    return "\n".join(lines) + "\n"


def render_review_human_summary(
    *,
    review_status: str,
    review_result: dict[str, Any] | None,
    failure_code: str | None = None,
    review_result_path: str | None = None,
) -> str:
    conflicts = review_result.get("conflicts") if isinstance(review_result, dict) else None
    drift = review_result.get("drift") if isinstance(review_result, dict) else None
    conflict_status = str(conflicts.get("status")) if isinstance(conflicts, dict) else "not_run"
    drift_status = str(drift.get("status")) if isinstance(drift, dict) else "not_run"
    summary = conflicts.get("summary") if isinstance(conflicts, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    drift_summary = drift.get("summary") if isinstance(drift, dict) else {}
    if not isinstance(drift_summary, dict):
        drift_summary = {}
    lines = [
        f"REVIEW: {review_status.upper()}",
        f"CONFLICTS: {conflict_status}",
        f"DRIFT: {drift_status}",
        "Writes performed: 0",
        (
            f"Unresolved: {int(summary.get('unresolved_conflict', 0))} "
            f"Blocked: {int(summary.get('reconciliation_blocked', 0))} "
            f"Unexpected drift: {int(drift_summary.get('unexpected_drift', 0))}"
        ),
    ]
    if review_result_path:
        lines.append(f"Artifact: {format_human_value(review_result_path)}")
    if failure_code:
        lines.append(f"Failure: {format_human_value(failure_code)}")
    return "\n".join(lines) + "\n"


def build_review_annotations(
    *,
    review_status: str,
    review_result: dict[str, Any] | None = None,
    drift_result: dict[str, Any] | None = None,
    failure_code: str | None = None,
) -> list[str]:
    if review_status == "failed":
        code = failure_code or "action_contract_invalid"
        message = f"governance review failed: {code}"
        return [f"::error::{escape_workflow_command(format_human_value(message))}"]

    annotations: list[str] = []
    conflicts = review_result.get("conflicts") if isinstance(review_result, dict) else None
    if isinstance(conflicts, dict) and conflicts.get("status") != "not_run":
        report = conflicts.get("conflict_report")
        assessments = conflicts.get("reconciliation_assessments")
        results = report.get("results") if isinstance(report, dict) else []
        if not isinstance(results, list):
            results = []
        if not isinstance(assessments, list):
            assessments = []
        for index, result in enumerate(results):
            if not isinstance(result, dict):
                continue
            assessment = assessments[index] if index < len(assessments) else {}
            if not isinstance(assessment, dict):
                assessment = {}
            state = str(result.get("state", ""))
            obj = result.get("object")
            if not isinstance(obj, dict):
                continue
            kind = str(obj.get("kind", ""))
            logical_id = str(obj.get("logical_id", ""))
            prop = str(result.get("property", ""))
            reason = str(result.get("reason", ""))
            blocks = bool(assessment.get("applicable")) and not bool(assessment.get("safe", True))
            if blocks:
                message = (
                    f"governance review reconciliation blocked: "
                    f"kind={kind} logical_id={logical_id} property={prop} "
                    f"state={state} reason={reason}"
                )
                annotations.append(
                    f"::error::{escape_workflow_command(format_human_value(message))}"
                )
            elif state in {"UNRESOLVED_CONFLICT", "INVALID_OR_AMBIGUOUS_AUTHORITY"}:
                message = (
                    f"governance review conflict finding: "
                    f"kind={kind} logical_id={logical_id} property={prop} "
                    f"state={state} reason={reason}"
                )
                annotations.append(
                    f"::warning::{escape_workflow_command(format_human_value(message))}"
                )

    if isinstance(drift_result, dict) and drift_result.get("status") == "unexpected_drift":
        for line in _review_drift_item_lines(drift_result):
            body = line[2:] if line.startswith("- ") else line
            message = f"governance review unexpected drift: {body}"
            annotations.append(f"::error::{escape_workflow_command(format_human_value(message))}")

    if len(annotations) <= MAX_ANNOTATIONS:
        return annotations
    kept = annotations[: MAX_ANNOTATIONS - 1]
    omitted = len(annotations) - len(kept)
    kept.append(
        "::warning::"
        + escape_workflow_command(
            format_human_value(
                f"governance review annotations truncated; omitted {omitted} additional items"
            )
        )
    )
    return kept
