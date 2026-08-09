"""Deterministic Markdown reports and workflow annotation preparation."""

from __future__ import annotations

import html
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
