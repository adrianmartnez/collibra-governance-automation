"""Explain metadata authority/conflict decisions (read-only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from governance.domain.authority import NormalizedAuthorityPolicySet
from governance.domain.conflicts import PropertyConflictReport, analyze_property_conflicts
from governance.domain.graph import GraphNodeIdentity
from governance.domain.observations import PropertyPath
from governance.identity.canonicalize import canonical_json_bytes
from governance.identity.hashing import explain_result_identity
from governance.identity.json_values import canonical_value_fingerprint
from governance.impact.contracts import format_human_value, identity_from_dict
from governance.io.atomic import atomic_write_text
from governance.reconciliation.errors import (
    CODE_INVALID_OBJECT_IDENTITY,
    CODE_NAMESPACE_MISMATCH,
    CODE_PARSE_ERROR,
    CODE_READ_ERROR,
    CODE_UNKNOWN_OBJECT,
    CODE_UNKNOWN_PROPERTY,
    CODE_WRITE_ERROR,
    DiagnosticError,
    ExplainError,
)
from governance.reconciliation.safety import assess_reconciliation
from governance.reconciliation.sources import ReconciliationSourceBundle

EXPLAIN_SCHEMA = "governance-explain-result"
EXPLAIN_VERSION = "1"


def load_object_identity(path: str | Path, *, namespace: str) -> GraphNodeIdentity:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExplainError(
            [
                DiagnosticError(
                    code=CODE_READ_ERROR,
                    path="/object_identity",
                    message="unable to read object identity file",
                )
            ]
        ) from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExplainError(
            [
                DiagnosticError(
                    code=CODE_PARSE_ERROR,
                    path="/object_identity",
                    message="invalid JSON in object identity file",
                )
            ]
        ) from exc
    if not isinstance(document, dict):
        raise ExplainError(
            [
                DiagnosticError(
                    code=CODE_INVALID_OBJECT_IDENTITY,
                    path="/object_identity",
                    message="object identity root must be a mapping",
                )
            ]
        )
    allowed = {"namespace", "kind", "logical_id", "parent"}
    if set(document.keys()) != allowed:
        raise ExplainError(
            [
                DiagnosticError(
                    code=CODE_INVALID_OBJECT_IDENTITY,
                    path="/object_identity",
                    message="object identity keys must be exactly namespace, kind, logical_id, parent",
                )
            ]
        )
    try:
        identity = identity_from_dict(document)
    except Exception as exc:
        raise ExplainError(
            [
                DiagnosticError(
                    code=CODE_INVALID_OBJECT_IDENTITY,
                    path="/object_identity",
                    message="object identity is invalid",
                )
            ]
        ) from exc
    if identity.namespace != namespace:
        raise ExplainError(
            [
                DiagnosticError(
                    code=CODE_NAMESPACE_MISMATCH,
                    path="/object_identity/namespace",
                    message="object identity namespace does not match --namespace",
                )
            ]
        )
    current: GraphNodeIdentity | None = identity
    while current is not None:
        if current.namespace != namespace:
            raise ExplainError(
                [
                    DiagnosticError(
                        code=CODE_NAMESPACE_MISMATCH,
                        path="/object_identity/namespace",
                        message="object identity ancestor namespace does not match --namespace",
                    )
                ]
            )
        current = current.parent
    return identity


def _object_known(bundle: ReconciliationSourceBundle, identity: GraphNodeIdentity) -> bool:
    key = identity.canonical_bytes()
    if any(item.canonical_bytes() == key for item in bundle.known_objects):
        return True
    return any(
        obs.object_identity.canonical_bytes() == key for obs in bundle.observations.observations
    )


def build_explain_result(
    *,
    namespace: str,
    identity: GraphNodeIdentity,
    bundle: ReconciliationSourceBundle,
    authority: NormalizedAuthorityPolicySet,
    property_filter: PropertyPath | None = None,
) -> dict[str, Any]:
    if not _object_known(bundle, identity):
        raise ExplainError(
            [
                DiagnosticError(
                    code=CODE_UNKNOWN_OBJECT,
                    path="/object_identity",
                    message="governed object was not found in reconciliation sources",
                )
            ]
        )

    report = analyze_property_conflicts(bundle.observations, authority)
    object_results = [
        result
        for result in report.results
        if result.object_identity.canonical_bytes() == identity.canonical_bytes()
    ]
    object_results.sort(key=lambda item: item.property_path.to_pointer())

    if property_filter is not None:
        matched = [
            result
            for result in object_results
            if result.property_path.to_pointer() == property_filter.to_pointer()
        ]
        if not matched:
            raise ExplainError(
                [
                    DiagnosticError(
                        code=CODE_UNKNOWN_PROPERTY,
                        path="/property",
                        message="property was not observed for the selected object",
                    )
                ]
            )
        object_results = matched

    properties: list[dict[str, Any]] = []
    for result in object_results:
        assessment = assess_reconciliation(result)
        entry: dict[str, Any] = {
            "has_effective_value": result.has_effective_value,
            "property": result.property_path.to_pointer(),
            "reason": result.reason,
            "reconciliation_applicable": assessment.applicable,
            "reconciliation_reason": assessment.reason,
            "reconciliation_safe": assessment.safe,
            "state": result.state,
            "value_groups": [group.to_value_group_dict() for group in result.value_groups],
        }
        if result.has_effective_value:
            entry["effective_value"] = result.effective_value
        if result.winning_rule_key is not None:
            entry["winning_rule_key"] = result.winning_rule_key.to_dict()
        properties.append(entry)

    without_identity = {
        "explain_schema": EXPLAIN_SCHEMA,
        "explain_version": EXPLAIN_VERSION,
        "namespace": namespace,
        "object": identity.to_dict(),
        "properties": properties,
        "writes_performed": 0,
    }
    identity_value = explain_result_identity(without_identity)
    return {**without_identity, "content_identity": identity_value.to_dict()}


def format_explain_human(result: dict[str, Any]) -> str:
    lines: list[str] = []
    obj = result["object"]
    lines.append(
        "OBJECT "
        f"namespace={format_human_value(str(obj['namespace']))} "
        f"kind={format_human_value(str(obj['kind']))} "
        f"logical_id={format_human_value(str(obj['logical_id']))}"
    )
    for prop in result["properties"]:
        if prop["has_effective_value"]:
            effective = format_human_value(
                json.dumps(prop["effective_value"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            )
            effective_repr = f"effective_value={effective}"
        else:
            effective_repr = "effective_value=<none>"
        lines.append(
            "PROPERTY "
            f"property={format_human_value(str(prop['property']))} "
            f"state={format_human_value(str(prop['state']))} "
            f"reason={format_human_value(str(prop['reason']))} "
            f"reconciliation_applicable={prop['reconciliation_applicable']} "
            f"reconciliation_safe={prop['reconciliation_safe']} "
            f"reconciliation_reason={format_human_value(str(prop['reconciliation_reason']))} "
            f"{effective_repr}"
        )
        winning = prop.get("winning_rule_key")
        if winning is not None:
            lines.append(
                "AUTHORITY "
                f"rule={format_human_value(json.dumps(winning, sort_keys=True, separators=(',', ':'), ensure_ascii=True))}"
            )
        effective_fp = None
        if prop["has_effective_value"]:
            effective_fp = canonical_value_fingerprint(prop["effective_value"])
        for group in prop["value_groups"]:
            value_json = json.dumps(
                group["value"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            is_effective = bool(
                effective_fp is not None
                and canonical_value_fingerprint(group["value"]) == effective_fp
            )
            lines.append(
                "VALUE_GROUP "
                f"value={format_human_value(value_json)} "
                f"effective={'true' if is_effective else 'false'}"
            )
            for provenance in group.get("provenance") or []:
                source_version = provenance.get("source_version")
                version_text = "null" if source_version is None else str(source_version)
                lines.append(
                    "PROVENANCE "
                    f"provider_type={format_human_value(str(provenance['provider_type']))} "
                    f"source_ref={format_human_value(str(provenance['source_ref']))} "
                    f"source_version={format_human_value(version_text)} "
                    f"observation_mode={format_human_value(str(provenance['observation_mode']))}"
                )
    lines.append("writes=0")
    return "\n".join(lines) + "\n"


def write_explain_artifact(result: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    payload = (
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    )
    try:
        return atomic_write_text(target, payload)
    except OSError as exc:
        raise ExplainError(
            [
                DiagnosticError(
                    code=CODE_WRITE_ERROR,
                    path="/output",
                    message="unable to write explain artifact",
                )
            ]
        ) from exc


def canonical_explain_json(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
