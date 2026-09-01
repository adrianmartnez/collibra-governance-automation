"""Human/JSON serialization and artifact writes for snapshot comparison."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from governance.comparison.errors import CODE_WRITE_ERROR, ComparisonError, DiagnosticError
from governance.impact.contracts import format_human_value
from governance.io.atomic import atomic_write_text


def canonical_comparison_json(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_comparison_artifact(result: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    try:
        return atomic_write_text(target, canonical_comparison_json(result))
    except OSError as exc:
        raise ComparisonError(
            [
                DiagnosticError(
                    code=CODE_WRITE_ERROR,
                    path="/output",
                    message="unable to write comparison artifact",
                )
            ]
        ) from exc


def format_comparison_human(result: dict[str, Any]) -> str:
    lines: list[str] = []
    baseline = result["baseline"]
    candidate = result["candidate"]
    lines.append(
        "baseline "
        f"source_name={format_human_value(str(baseline['source_name']))} "
        f"database_name={format_human_value(str(baseline['database_name']))} "
        f"identity={format_human_value(str(baseline['content_identity']['digest']))}"
    )
    lines.append(
        "candidate "
        f"source_name={format_human_value(str(candidate['source_name']))} "
        f"database_name={format_human_value(str(candidate['database_name']))} "
        f"identity={format_human_value(str(candidate['content_identity']['digest']))}"
    )

    alignment = result["root_alignment"]
    align_parts: list[str] = []
    if alignment.get("source") is not None:
        source = alignment["source"]
        align_parts.append(
            "source_baseline="
            + format_human_value(str(source["baseline"]))
            + " source_candidate="
            + format_human_value(str(source["candidate"]))
        )
    if alignment.get("database") is not None:
        database = alignment["database"]
        align_parts.append(
            "database_baseline="
            + format_human_value(str(database["baseline"]))
            + " database_candidate="
            + format_human_value(str(database["candidate"]))
        )
    if align_parts:
        lines.append("root_alignment " + " ".join(align_parts))
    else:
        lines.append("root_alignment none")

    summary = result["summary"]
    lines.append(
        f"status={format_human_value(str(result['status']))} "
        f"summary added={summary['added']} removed={summary['removed']} "
        f"changed={summary['changed']} unchanged={summary['unchanged']} "
        f"property_changes={summary['property_changes']}"
    )

    for item in result["object_changes"]:
        identity_json = json.dumps(
            item["object_identity"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        kind = item["object_identity"]["kind"]
        change = item["change"]
        if change != "changed":
            lines.append(
                f"OBJECT {change} kind={format_human_value(kind)} "
                f"identity={format_human_value(identity_json)}"
            )
            continue
        for prop in item["property_changes"]:
            before = _format_side(prop["baseline"])
            after = _format_side(prop["candidate"])
            lines.append(
                f"OBJECT changed kind={format_human_value(kind)} "
                f"identity={format_human_value(identity_json)} "
                f"property={format_human_value(str(prop['property']))} "
                f"before={before} after={after}"
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
