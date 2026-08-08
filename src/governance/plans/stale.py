"""Stale-plan comparison helpers and result contracts."""

from __future__ import annotations

from typing import Any

from governance.identity.hashing import ContentIdentity
from governance.plans.errors import STALE_RESULT_SCHEMA, STALE_RESULT_VERSION


def identity_mismatch(
    *,
    category: str,
    expected: ContentIdentity,
    observed: ContentIdentity,
    message: str,
) -> dict[str, Any]:
    return {
        "category": category,
        "expected_identity": expected.to_dict(),
        "kind": "identity",
        "message": message,
        "observed_identity": observed.to_dict(),
    }


def version_mismatch(
    *,
    category: str,
    expected: str,
    observed: str,
    message: str,
) -> dict[str, Any]:
    return {
        "category": category,
        "expected_version": expected,
        "kind": "version",
        "message": message,
        "observed_version": observed,
    }


def build_stale_result(mismatches: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        mismatches,
        key=lambda item: (item["category"], item["kind"], item["message"]),
    )
    return {
        "mismatches": ordered,
        "result_schema": STALE_RESULT_SCHEMA,
        "result_version": STALE_RESULT_VERSION,
        "stale": True,
    }


def format_stale_human(result: dict[str, Any]) -> str:
    lines = ["stale=true", f"mismatches={len(result['mismatches'])}"]
    for item in result["mismatches"]:
        if item["kind"] == "identity":
            lines.append(
                f"mismatch category={item['category']} "
                f"expected={item['expected_identity']['digest']} "
                f"observed={item['observed_identity']['digest']} "
                f"message={item['message']}"
            )
        else:
            lines.append(
                f"mismatch category={item['category']} "
                f"expected={item['expected_version']} "
                f"observed={item['observed_version']} "
                f"message={item['message']}"
            )
    return "\n".join(lines) + "\n"
