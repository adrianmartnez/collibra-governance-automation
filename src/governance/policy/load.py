"""Load, validate, and normalize governance policy files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from governance.config_contract.models import CanonicalConfig
from governance.config_contract.paths import normalize_relative_path
from governance.policy.errors import (
    CODE_DUPLICATE,
    CODE_MISSING,
    CODE_SEMANTIC,
    PolicyDiagnosticError,
    PolicySemanticError,
)
from governance.policy.models import (
    DESCRIPTION_KINDS,
    OWNER_KINDS,
    RELATIONSHIP_KINDS,
    NormalizedPolicy,
    NormalizedPolicySet,
    PolicySelector,
)
from governance.policy.parse import parse_policy_yaml
from governance.policy.schema import validate_policy_structure

_RULE_ALLOWED_KINDS = {
    "require_owner": OWNER_KINDS,
    "require_description": DESCRIPTION_KINDS,
    "require_relationship": RELATIONSHIP_KINDS,
}


def load_normalized_policies(canonical: CanonicalConfig) -> NormalizedPolicySet:
    """Load policies referenced by CanonicalConfig. Empty files => empty set."""
    if not canonical.policies.files:
        return NormalizedPolicySet()

    collected: list[NormalizedPolicy] = []
    seen_ids: dict[str, str] = {}
    errors: list[PolicyDiagnosticError] = []

    for index, relative in enumerate(canonical.policies.files):
        source = relative.replace("\\", "/")
        try:
            normalize_relative_path(relative, pointer=f"/policies/files/{index}")
        except Exception:
            # Path already validated by config contract; keep fail-closed.
            errors.append(
                PolicyDiagnosticError(
                    code=CODE_SEMANTIC,
                    path="",
                    message="policy path is not safe",
                    source=source,
                )
            )
            continue

        absolute = Path(canonical.config_root) / relative
        if not absolute.is_file():
            errors.append(
                PolicyDiagnosticError(
                    code=CODE_MISSING,
                    path="",
                    message="policy file does not exist",
                    source=source,
                )
            )
            continue

        document = parse_policy_yaml(absolute, source=source)
        validate_policy_structure(document, source=source)
        file_policies, file_errors = _normalize_document(document, source=source)
        errors.extend(file_errors)
        for policy in file_policies:
            if policy.id in seen_ids:
                errors.append(
                    PolicyDiagnosticError(
                        code=CODE_DUPLICATE,
                        path="/policies",
                        message="duplicate policy id",
                        source=source,
                    )
                )
            else:
                seen_ids[policy.id] = source
                collected.append(policy)

    if errors:
        raise PolicySemanticError(errors)
    return NormalizedPolicySet(policies=tuple(collected))


def _normalize_document(
    document: dict[str, Any],
    *,
    source: str,
) -> tuple[list[NormalizedPolicy], list[PolicyDiagnosticError]]:
    policies_raw = document.get("policies")
    if not isinstance(policies_raw, list):
        return [], [
            PolicyDiagnosticError(
                code=CODE_SEMANTIC,
                path="/policies",
                message="policies must be an array",
                source=source,
            )
        ]

    normalized: list[NormalizedPolicy] = []
    errors: list[PolicyDiagnosticError] = []
    for index, item in enumerate(policies_raw):
        pointer = f"/policies/{index}"
        if not isinstance(item, dict):
            errors.append(
                PolicyDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=pointer,
                    message="policy must be a mapping",
                    source=source,
                )
            )
            continue
        try:
            normalized.append(_normalize_policy(item, pointer=pointer, source=source))
        except PolicySemanticError as exc:
            errors.extend(exc.errors)
    return normalized, errors


def _normalize_policy(
    item: dict[str, Any],
    *,
    pointer: str,
    source: str,
) -> NormalizedPolicy:
    policy_id = item.get("id")
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise PolicySemanticError(
            [
                PolicyDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/id",
                    message="policy id is required",
                    source=source,
                )
            ]
        )
    severity = item.get("severity")
    if severity not in {"error", "warning"}:
        raise PolicySemanticError(
            [
                PolicyDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/severity",
                    message="severity must be error or warning",
                    source=source,
                )
            ]
        )
    rule = item.get("rule")
    if not isinstance(rule, dict):
        raise PolicySemanticError(
            [
                PolicyDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/rule",
                    message="rule must be a mapping",
                    source=source,
                )
            ]
        )
    rule_type = rule.get("type")
    if rule_type not in _RULE_ALLOWED_KINDS:
        raise PolicySemanticError(
            [
                PolicyDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/rule/type",
                    message="unsupported rule type",
                    source=source,
                )
            ]
        )
    select_raw = rule.get("select")
    if not isinstance(select_raw, dict):
        raise PolicySemanticError(
            [
                PolicyDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/rule/select",
                    message="select must be a mapping",
                    source=source,
                )
            ]
        )
    selector = _normalize_selector(
        select_raw,
        rule_type=rule_type,
        pointer=f"{pointer}/rule/select",
        source=source,
    )
    description = item.get("description")
    if description is not None:
        if not isinstance(description, str) or not description.strip():
            raise PolicySemanticError(
                [
                    PolicyDiagnosticError(
                        code=CODE_SEMANTIC,
                        path=f"{pointer}/description",
                        message="description must be a non-empty string when provided",
                        source=source,
                    )
                ]
            )
        description = description.strip()

    return NormalizedPolicy(
        id=policy_id.strip(),
        severity=severity,
        rule_type=rule_type,
        select=selector,
        description=description,
    )


def _normalize_selector(
    select_raw: dict[str, Any],
    *,
    rule_type: str,
    pointer: str,
    source: str,
) -> PolicySelector:
    kind = select_raw.get("kind")
    if kind not in {
        "data_source",
        "database",
        "schema",
        "table",
        "column",
        "relationship",
    }:
        raise PolicySemanticError(
            [
                PolicyDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/kind",
                    message="unsupported object kind",
                    source=source,
                )
            ]
        )
    if kind not in _RULE_ALLOWED_KINDS[rule_type]:
        raise PolicySemanticError(
            [
                PolicyDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/kind",
                    message=f"kind is not valid for rule type {rule_type}",
                    source=source,
                )
            ]
        )

    object_id = select_raw.get("id")
    id_prefix = select_raw.get("id_prefix")
    if object_id is not None and id_prefix is not None:
        raise PolicySemanticError(
            [
                PolicyDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=pointer,
                    message="id and id_prefix are mutually exclusive",
                    source=source,
                )
            ]
        )
    if object_id is not None:
        if not isinstance(object_id, str) or not object_id.strip():
            raise PolicySemanticError(
                [
                    PolicyDiagnosticError(
                        code=CODE_SEMANTIC,
                        path=f"{pointer}/id",
                        message="id must be a non-empty string",
                        source=source,
                    )
                ]
            )
        object_id = object_id.strip()
    if id_prefix is not None:
        if not isinstance(id_prefix, str) or not id_prefix.strip():
            raise PolicySemanticError(
                [
                    PolicyDiagnosticError(
                        code=CODE_SEMANTIC,
                        path=f"{pointer}/id_prefix",
                        message="id_prefix must be a non-empty string",
                        source=source,
                    )
                ]
            )
        id_prefix = id_prefix.strip()

    return PolicySelector(kind=kind, object_id=object_id, id_prefix=id_prefix)
