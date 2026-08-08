"""Policy evaluation report contracts."""

from __future__ import annotations

from typing import Any

from governance.identity.hashing import ContentIdentity
from governance.policy.models import PolicyViolation

REPORT_SCHEMA = "governance-policy-report"
REPORT_VERSION = "1"


def build_policy_report(
    *,
    violations: tuple[PolicyViolation, ...],
    policy_identity: ContentIdentity,
    snapshot_identity: ContentIdentity,
) -> dict[str, Any]:
    has_errors = any(item.severity == "error" for item in violations)
    return {
        "ok": not has_errors,
        "policy_identity": policy_identity.to_dict(),
        "report_schema": REPORT_SCHEMA,
        "report_version": REPORT_VERSION,
        "snapshot_identity": snapshot_identity.to_dict(),
        "violations": [item.to_dict() for item in violations],
    }


def format_policy_report_human(report: dict[str, Any]) -> str:
    lines = [f"ok={str(report['ok']).lower()}"]
    lines.append(f"violations={len(report['violations'])}")
    for item in report["violations"]:
        name = item.get("object_name")
        name_part = f" name={name}" if name else ""
        lines.append(
            f"{item['severity']} policy={item['policy_id']} "
            f"rule={item['rule_type']} kind={item['object_kind']} "
            f"id={item['object_id']}{name_part} reason={item['reason']}"
        )
    return "\n".join(lines) + "\n"
