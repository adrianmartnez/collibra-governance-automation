"""Versioned impact changes/result contracts, diagnostics, and human formatting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from governance.domain.graph import GovernanceGraph, GraphNodeIdentity
from governance.domain.impact import GovernanceImpactResult
from governance.identity.hashing import ContentIdentity, impact_result_identity
from governance.impact.policy import AffectedPolicy, affected_policies_to_dicts
from governance.io.atomic import atomic_write_text
from governance.policy.models import NormalizedPolicySet

try:
    from importlib.resources import files
except ImportError:  # pragma: no cover
    from importlib_resources import files  # type: ignore[no-redef]

CHANGES_SCHEMA = "governance-impact-changes"
CHANGES_VERSION = "1"
RESULT_SCHEMA = "governance-impact-result"
RESULT_VERSION = "1"

IMPACT_DIAGNOSTIC_SCHEMA = "governance-impact-diagnostics"
IMPACT_DIAGNOSTIC_VERSION = "1"

CODE_READ = "read_error"
CODE_PARSE = "parse_error"
CODE_SCHEMA = "schema_error"
CODE_UNSUPPORTED = "unsupported_version"
CODE_SOURCE = "source_error"
CODE_GRAPH_CONFLICT = "graph_conflict"
CODE_CHANGED_NODE = "changed_node_error"
CODE_INTEGRITY = "integrity_error"

_CHANGES_RESOURCE = "governance-impact-changes.v1.schema.json"
_RESULT_RESOURCE = "governance-impact-result.v1.schema.json"

_changes_validator: Draft202012Validator | None = None
_result_validator: Draft202012Validator | None = None


@dataclass(frozen=True, slots=True)
class ImpactDiagnosticError:
    code: str
    message: str
    path: str = ""
    source_kind: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }
        if self.source_kind is not None:
            payload["source_kind"] = self.source_kind
        return payload


class ImpactError(Exception):
    def __init__(
        self, errors: list[ImpactDiagnosticError] | tuple[ImpactDiagnosticError, ...]
    ) -> None:
        self.errors = tuple(
            sorted(
                errors,
                key=lambda e: (e.path, e.code, e.message, e.source_kind or ""),
            )
        )
        super().__init__(self.errors[0].message if self.errors else "impact error")


class ImpactReadError(ImpactError):
    pass


class ImpactParseError(ImpactError):
    pass


class ImpactSchemaError(ImpactError):
    pass


class UnsupportedImpactVersionError(ImpactError):
    pass


class ImpactIntegrityError(ImpactError):
    pass


class ImpactSourceError(ImpactError):
    pass


class ImpactGraphConflictError(ImpactError):
    pass


class ImpactChangedNodeError(ImpactError):
    pass


def impact_diagnostics_failure(
    errors: list[ImpactDiagnosticError] | tuple[ImpactDiagnosticError, ...],
) -> dict[str, Any]:
    ordered = sorted(
        errors,
        key=lambda e: (e.path, e.code, e.message, e.source_kind or ""),
    )
    return {
        "diagnostic_schema": IMPACT_DIAGNOSTIC_SCHEMA,
        "diagnostic_version": IMPACT_DIAGNOSTIC_VERSION,
        "errors": [error.to_dict() for error in ordered],
        "ok": False,
    }


def load_changes_schema() -> dict[str, Any]:
    text = (
        files("governance.impact.schemas").joinpath(_CHANGES_RESOURCE).read_text(encoding="utf-8")
    )
    return json.loads(text)


def load_result_schema() -> dict[str, Any]:
    text = files("governance.impact.schemas").joinpath(_RESULT_RESOURCE).read_text(encoding="utf-8")
    return json.loads(text)


def _get_changes_validator() -> Draft202012Validator:
    global _changes_validator
    if _changes_validator is None:
        _changes_validator = Draft202012Validator(load_changes_schema())
    return _changes_validator


def _get_result_validator() -> Draft202012Validator:
    global _result_validator
    if _result_validator is None:
        _result_validator = Draft202012Validator(load_result_schema())
    return _result_validator


def _pointer_from_path(path: list[Any]) -> str:
    if not path:
        return ""
    parts = [str(item).replace("~", "~0").replace("/", "~1") for item in path]
    return "/" + "/".join(parts)


def _raise_schema_errors(
    document: Any,
    *,
    validator: Draft202012Validator,
    version_field: str,
    unsupported_cls: type[ImpactError],
    schema_cls: type[ImpactError],
) -> None:
    if not isinstance(document, dict):
        raise schema_cls(
            [
                ImpactDiagnosticError(
                    code=CODE_SCHEMA,
                    path="",
                    message="document root must be a mapping",
                )
            ]
        )
    version = document.get(version_field)
    if version is not None and version != "1":
        raise unsupported_cls(
            [
                ImpactDiagnosticError(
                    code=CODE_UNSUPPORTED,
                    path=f"/{version_field}",
                    message=f"unsupported {version_field}",
                )
            ]
        )

    errors: list[ImpactDiagnosticError] = []
    for error in sorted(
        validator.iter_errors(document),
        key=lambda err: list(err.absolute_path),
    ):
        path = _pointer_from_path(list(error.absolute_path))
        if error.validator == "const" and list(error.absolute_path) == [version_field]:
            raise unsupported_cls(
                [
                    ImpactDiagnosticError(
                        code=CODE_UNSUPPORTED,
                        path=path,
                        message=f"unsupported {version_field}",
                    )
                ]
            )
        errors.append(
            ImpactDiagnosticError(
                code=CODE_SCHEMA,
                path=path,
                message=error.message,
            )
        )
    if errors:
        raise schema_cls(errors)


def validate_changes_document(document: Any) -> None:
    _raise_schema_errors(
        document,
        validator=_get_changes_validator(),
        version_field="changes_version",
        unsupported_cls=UnsupportedImpactVersionError,
        schema_cls=ImpactSchemaError,
    )
    if document.get("changes_schema") != CHANGES_SCHEMA:
        raise ImpactSchemaError(
            [
                ImpactDiagnosticError(
                    code=CODE_SCHEMA,
                    path="/changes_schema",
                    message="changes_schema must be governance-impact-changes",
                )
            ]
        )


def validate_result_document(document: Any) -> None:
    _raise_schema_errors(
        document,
        validator=_get_result_validator(),
        version_field="result_version",
        unsupported_cls=UnsupportedImpactVersionError,
        schema_cls=ImpactSchemaError,
    )
    if document.get("result_schema") != RESULT_SCHEMA:
        raise ImpactSchemaError(
            [
                ImpactDiagnosticError(
                    code=CODE_SCHEMA,
                    path="/result_schema",
                    message="result_schema must be governance-impact-result",
                )
            ]
        )


def identity_from_dict(payload: dict[str, Any]) -> GraphNodeIdentity:
    """Rebuild GraphNodeIdentity recursively; domain constructor is semantic authority."""
    if not isinstance(payload, dict):
        raise ImpactChangedNodeError(
            [
                ImpactDiagnosticError(
                    code=CODE_CHANGED_NODE,
                    path="",
                    message="changed node identity must be a mapping",
                )
            ]
        )
    parent_raw = payload.get("parent")
    parent: GraphNodeIdentity | None
    if parent_raw is None:
        parent = None
    else:
        if not isinstance(parent_raw, dict):
            raise ImpactChangedNodeError(
                [
                    ImpactDiagnosticError(
                        code=CODE_CHANGED_NODE,
                        path="/parent",
                        message="parent must be null or a mapping",
                    )
                ]
            )
        parent = identity_from_dict(parent_raw)
    try:
        return GraphNodeIdentity(
            str(payload["namespace"]),
            str(payload["kind"]),
            str(payload["logical_id"]),
            parent=parent,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ImpactChangedNodeError(
            [
                ImpactDiagnosticError(
                    code=CODE_CHANGED_NODE,
                    path="",
                    message="changed node identity is invalid",
                )
            ]
        ) from exc


def _validate_namespace(
    identities: list[GraphNodeIdentity],
    *,
    expected_namespace: str,
) -> None:
    for identity in identities:
        chain = (identity, *identity.ancestors())
        for node in chain:
            if node.namespace != expected_namespace:
                raise ImpactChangedNodeError(
                    [
                        ImpactDiagnosticError(
                            code=CODE_CHANGED_NODE,
                            path="/changed_nodes",
                            message=(
                                "changed node namespace must match --namespace "
                                f"(expected {expected_namespace!r})"
                            ),
                        )
                    ]
                )


def load_impact_changes(
    path: str | Path,
    *,
    expected_namespace: str,
) -> tuple[GraphNodeIdentity, ...]:
    """Load and validate governance-impact-changes v1; return GraphNodeIdentity roots."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ImpactReadError(
            [
                ImpactDiagnosticError(
                    code=CODE_READ,
                    path="",
                    message="unable to read impact changes file",
                )
            ]
        ) from exc
    except UnicodeDecodeError as exc:
        raise ImpactParseError(
            [
                ImpactDiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="invalid UTF-8 in impact changes file",
                )
            ]
        ) from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImpactParseError(
            [
                ImpactDiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="invalid JSON in impact changes file",
                )
            ]
        ) from exc

    validate_changes_document(document)
    identities: list[GraphNodeIdentity] = []
    for index, raw in enumerate(document["changed_nodes"]):
        try:
            identities.append(identity_from_dict(raw))
        except ImpactChangedNodeError as exc:
            # Re-home path when possible.
            raised = [
                ImpactDiagnosticError(
                    code=item.code,
                    message=item.message,
                    path=item.path or f"/changed_nodes/{index}",
                    source_kind=item.source_kind,
                )
                for item in exc.errors
            ]
            raise ImpactChangedNodeError(raised) from exc
    _validate_namespace(identities, expected_namespace=expected_namespace)
    return tuple(identities)


def build_impact_result(
    *,
    graph: GovernanceGraph,
    impact: GovernanceImpactResult,
    affected_policies: tuple[AffectedPolicy, ...] | list[AffectedPolicy] = (),
    policy_set: NormalizedPolicySet | None = None,
) -> dict[str, Any]:
    """Build canonical governance-impact-result payload including content_identity."""
    impact_detected = bool(impact.direct_nodes or impact.transitive_nodes)
    status = "impacted" if impact_detected else "clear"
    policy_identity: dict[str, str] | None
    if policy_set is None:
        policy_identity = None
    else:
        from governance.identity.hashing import policy_identity as compute_policy_identity

        policy_identity = compute_policy_identity(policy_set.to_identity_dict()).to_dict()

    without_identity: dict[str, Any] = {
        "affected_policies": affected_policies_to_dicts(tuple(affected_policies)),
        "graph_identity": graph.content_identity().to_dict(),
        "impact": impact.to_dict(),
        "impact_detected": impact_detected,
        "policy_identity": policy_identity,
        "result_schema": RESULT_SCHEMA,
        "result_version": RESULT_VERSION,
        "status": status,
        "writes_performed": 0,
    }
    identity = impact_result_identity(without_identity)
    payload = {**without_identity, "content_identity": identity.to_dict()}
    validate_result_document(payload)
    verify_impact_result_integrity(payload)
    return payload


def verify_impact_result_integrity(document: dict[str, Any]) -> ContentIdentity:
    """Recompute content identity; raise on mismatch."""
    validate_result_document(document)
    stored = document.get("content_identity")
    without = {key: value for key, value in document.items() if key != "content_identity"}
    recomputed = impact_result_identity(without)
    if not isinstance(stored, dict) or recomputed.to_dict() != {
        "algorithm": stored.get("algorithm"),
        "digest": stored.get("digest"),
        "hashing_contract_version": stored.get("hashing_contract_version"),
    }:
        raise ImpactIntegrityError(
            [
                ImpactDiagnosticError(
                    code=CODE_INTEGRITY,
                    path="/content_identity",
                    message="impact result content identity mismatch",
                )
            ]
        )
    return recomputed


def load_impact_result(path: str | Path) -> dict[str, Any]:
    """Load, schema-validate, and integrity-check a governance-impact-result artifact."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ImpactReadError(
            [
                ImpactDiagnosticError(
                    code=CODE_READ,
                    path="",
                    message="unable to read impact result file",
                )
            ]
        ) from exc
    except UnicodeDecodeError as exc:
        raise ImpactParseError(
            [
                ImpactDiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="invalid UTF-8 in impact result file",
                )
            ]
        ) from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImpactParseError(
            [
                ImpactDiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="invalid JSON in impact result file",
                )
            ]
        ) from exc
    if not isinstance(document, dict):
        raise ImpactSchemaError(
            [
                ImpactDiagnosticError(
                    code=CODE_SCHEMA,
                    path="",
                    message="impact result root must be a mapping",
                )
            ]
        )
    verify_impact_result_integrity(document)
    return document


def canonical_impact_json(payload: dict[str, Any]) -> str:
    """Canonical machine serialization (compact, sorted keys, trailing newline)."""
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    )


def write_impact_result(path: str | Path, payload: dict[str, Any]) -> Path:
    """Validate and atomically persist a governance-impact-result artifact."""
    verify_impact_result_integrity(payload)
    return atomic_write_text(Path(path), canonical_impact_json(payload))


def format_human_value(value: str) -> str:
    """Deterministic single-line representation for human stdout dynamic fields.

    Escapes newlines, carriage returns, tabs, C0/DEL, and remaining non-printable
    Unicode (including line/paragraph separators and surrogates) so one logical
    record cannot inject a second physical line. Does not alter machine JSON.
    """
    if not isinstance(value, str):
        value = str(value)
    out: list[str] = []
    for ch in value:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\x{code:02x}")
        elif not ch.isprintable():
            if code < 0x100:
                out.append(f"\\x{code:02x}")
            elif code < 0x10000:
                out.append(f"\\u{code:04x}")
            else:
                out.append(f"\\U{code:08x}")
        else:
            out.append(ch)
    return "".join(out)


def _node_name(graph: GovernanceGraph, identity: GraphNodeIdentity) -> str:
    for node in graph.nodes:
        if node.identity == identity:
            return node.name
    return identity.logical_id


def format_impact_result_human(
    payload: dict[str, Any],
    *,
    graph: GovernanceGraph,
    output_path: str | Path,
) -> str:
    """Deterministic concise human summary; one logical record per line."""
    impact = payload["impact"]
    lines = [
        f"status={format_human_value(str(payload['status']))}",
        f"changed={len(impact['changed_nodes'])}",
        f"direct={len(impact['direct_nodes'])}",
        f"transitive={len(impact['transitive_nodes'])}",
        f"affected_edges={len(impact['affected_edges'])}",
        f"contracts={len(impact['associated_contracts'])}",
        f"governance_assets={len(impact['governance_assets'])}",
        f"policy_relevant_nodes={len(impact['policy_relevant_nodes'])}",
        f"affected_policies={len(payload['affected_policies'])}",
        "writes=0",
        f"artifact_written={format_human_value(str(output_path))}",
    ]

    path_by_target: dict[str, dict[str, Any]] = {}
    for path in impact["paths"]:
        key = json.dumps(path["target"], sort_keys=True, separators=(",", ":"))
        path_by_target[key] = path

    for node_dict in impact["direct_nodes"]:
        identity = identity_from_dict(node_dict)
        path = path_by_target.get(json.dumps(node_dict, sort_keys=True, separators=(",", ":")))
        distance = path["distance"] if path is not None else 1
        lines.append(
            "DIRECT "
            f"kind={format_human_value(identity.kind)} "
            f"logical_id={format_human_value(identity.logical_id)} "
            f"name={format_human_value(_node_name(graph, identity))} "
            f"distance={distance}"
        )

    for node_dict in impact["transitive_nodes"]:
        identity = identity_from_dict(node_dict)
        path = path_by_target.get(json.dumps(node_dict, sort_keys=True, separators=(",", ":")))
        distance = path["distance"] if path is not None else 0
        lines.append(
            "TRANSITIVE "
            f"kind={format_human_value(identity.kind)} "
            f"logical_id={format_human_value(identity.logical_id)} "
            f"name={format_human_value(_node_name(graph, identity))} "
            f"distance={distance}"
        )

    for policy in payload["affected_policies"]:
        lines.append(
            "POLICY "
            f"id={format_human_value(str(policy['policy_id']))} "
            f"severity={format_human_value(str(policy['severity']))} "
            f"rule={format_human_value(str(policy['rule_type']))} "
            f"matches={len(policy['matched_objects'])}"
        )

    for path in impact["paths"]:
        root = path["root"]
        target = path["target"]
        step_labels = [
            f"{format_human_value(step['traversal'])}:{format_human_value(step['edge']['kind'])}"
            for step in path["steps"]
        ]
        lines.append(
            "PATH "
            f"root={format_human_value(root['kind'])}/{format_human_value(root['logical_id'])} "
            f"target={format_human_value(target['kind'])}/"
            f"{format_human_value(target['logical_id'])} "
            f"distance={path['distance']} "
            f"steps={','.join(step_labels)}"
        )

    return "\n".join(lines) + "\n"
