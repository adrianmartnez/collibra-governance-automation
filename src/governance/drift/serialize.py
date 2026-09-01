"""Human/JSON serialization and artifact writes for drift results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from governance.drift.errors import CODE_WRITE_ERROR, DiagnosticError, DriftError
from governance.impact.contracts import format_human_value
from governance.io.atomic import atomic_write_text


def canonical_drift_json(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_drift_artifact(result: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    try:
        return atomic_write_text(target, canonical_drift_json(result))
    except OSError as exc:
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_WRITE_ERROR,
                    path="/output",
                    message="unable to write drift artifact",
                )
            ]
        ) from exc


def format_drift_human(result: dict[str, Any]) -> str:
    lines: list[str] = []
    comparison_digest = result["comparison_content_identity"]["digest"]
    lines.append(
        f"status={format_human_value(str(result['status']))} "
        f"comparison_identity={format_human_value(comparison_digest)}"
    )
    policy_identity = result.get("drift_policy_identity")
    if policy_identity is None:
        lines.append("policy_identity=none")
    else:
        lines.append(f"policy_identity={format_human_value(str(policy_identity['digest']))}")

    summary = result["summary"]
    lines.append(
        "summary "
        f"expected={summary['expected_differences']} "
        f"unexpected={summary['unexpected_drift']} "
        f"affected_objects={summary['affected_objects']} "
        f"property_drift={summary['property_drift']}"
    )

    for item in result["classified_changes"]:
        identity_json = json.dumps(
            item["object_identity"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        kind = item["object_identity"]["kind"]
        change = item["change"]
        classification = item["classification"]
        rule_ids_json = json.dumps(
            item["matched_rule_ids"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        if change == "changed":
            before = _format_side(item["baseline"])
            after = _format_side(item["candidate"])
            lines.append(
                f"CLASSIFIED {classification} change={format_human_value(change)} "
                f"kind={format_human_value(kind)} "
                f"identity={format_human_value(identity_json)} "
                f"property={format_human_value(str(item['property']))} "
                f"before={before} after={after} "
                f"reason={format_human_value(str(item['reason']))} "
                f"rule_ids={format_human_value(rule_ids_json)}"
            )
        else:
            lines.append(
                f"CLASSIFIED {classification} change={format_human_value(change)} "
                f"kind={format_human_value(kind)} "
                f"identity={format_human_value(identity_json)} "
                f"reason={format_human_value(str(item['reason']))} "
                f"rule_ids={format_human_value(rule_ids_json)}"
            )

    lines.append("writes=0")
    return "\n".join(lines) + "\n"


def _format_side(side: dict[str, Any]) -> str:
    if not side.get("has_value"):
        return "<missing>"
    value_json = json.dumps(
        side.get("value"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return format_human_value(value_json)
