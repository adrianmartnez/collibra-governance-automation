"""Load, validate, and normalize governance authority files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from governance.authority.errors import (
    CODE_AMBIGUOUS,
    CODE_DUPLICATE,
    CODE_MISSING,
    CODE_SEMANTIC,
    AuthorityDiagnosticError,
    AuthorityError,
    AuthoritySemanticError,
)
from governance.authority.parse import parse_authority_yaml
from governance.authority.schema import validate_authority_structure
from governance.config_contract.models import CanonicalConfig
from governance.config_contract.paths import normalize_relative_path
from governance.domain.authority import (
    AUTHORITY_NODE_KINDS,
    AuthorityDeclaration,
    AuthorityRuleKey,
    AuthoritySelector,
    AuthorityTarget,
    NormalizedAuthorityPolicySet,
    NormalizedAuthorityRule,
)
from governance.domain.observations import PropertyPath

_AUTHORITY_KIND_SET = frozenset(AUTHORITY_NODE_KINDS)


def load_normalized_authority(canonical: CanonicalConfig) -> NormalizedAuthorityPolicySet:
    """Load authority referenced by CanonicalConfig. Empty files => empty set."""
    if not canonical.authority.files:
        return NormalizedAuthorityPolicySet()

    collected: list[tuple[AuthorityRuleKey, AuthorityDeclaration]] = []
    seen_ids: dict[str, str] = {}
    errors: list[AuthorityDiagnosticError] = []

    for index, relative in enumerate(canonical.authority.files):
        source = relative.replace("\\", "/")
        try:
            normalize_relative_path(relative, pointer=f"/authority/files/{index}")
        except Exception:
            errors.append(
                AuthorityDiagnosticError(
                    code=CODE_SEMANTIC,
                    path="",
                    message="authority path is not safe",
                    source=source,
                    file_index=index,
                )
            )
            continue

        absolute = Path(canonical.config_root) / relative
        if not absolute.is_file():
            errors.append(
                AuthorityDiagnosticError(
                    code=CODE_MISSING,
                    path="",
                    message="authority file does not exist",
                    source=source,
                    file_index=index,
                )
            )
            continue

        try:
            document = parse_authority_yaml(absolute, source=source)
            validate_authority_structure(document, source=source)
            file_rules, file_errors = _normalize_document(document, source=source)
        except AuthorityError as exc:
            for item in exc.errors:
                errors.append(
                    AuthorityDiagnosticError(
                        code=item.code,
                        path=item.path,
                        message=item.message,
                        source=item.source or source,
                        file_index=index,
                    )
                )
            continue
        for item in file_errors:
            errors.append(
                AuthorityDiagnosticError(
                    code=item.code,
                    path=item.path,
                    message=item.message,
                    source=item.source or source,
                    file_index=index,
                )
            )
        for key, declaration in file_rules:
            if declaration.config_id in seen_ids:
                errors.append(
                    AuthorityDiagnosticError(
                        code=CODE_DUPLICATE,
                        path="/rules",
                        message="duplicate authority rule id",
                        source=source,
                        file_index=index,
                    )
                )
            else:
                seen_ids[declaration.config_id] = source
                collected.append((key, declaration))

    if errors:
        raise AuthoritySemanticError(errors)

    # Global duplicate id already enforced. Group by semantic key; detect
    # same-selector / different-target ambiguity.
    by_key: dict[AuthorityRuleKey, list[AuthorityDeclaration]] = {}
    selector_to_keys: dict[AuthoritySelector, set[AuthorityRuleKey]] = {}
    for key, declaration in collected:
        by_key.setdefault(key, []).append(declaration)
        selector_to_keys.setdefault(key.selector, set()).add(key)

    ambiguity_errors: list[AuthorityDiagnosticError] = []
    for selector, keys in selector_to_keys.items():
        if len(keys) > 1:
            ambiguity_errors.append(
                AuthorityDiagnosticError(
                    code=CODE_AMBIGUOUS,
                    path="/rules",
                    message=(
                        "ambiguous authority rules share the same selector with "
                        f"different targets for {selector.to_dict()!r}"
                    ),
                    source="",
                )
            )
    if ambiguity_errors:
        raise AuthoritySemanticError(ambiguity_errors)

    rules = tuple(
        NormalizedAuthorityRule(key=key, declarations=tuple(declarations))
        for key, declarations in by_key.items()
    )
    return NormalizedAuthorityPolicySet(rules=rules)


def _normalize_document(
    document: dict[str, Any],
    *,
    source: str,
) -> tuple[list[tuple[AuthorityRuleKey, AuthorityDeclaration]], list[AuthorityDiagnosticError]]:
    rules_raw = document.get("rules")
    if not isinstance(rules_raw, list):
        return [], [
            AuthorityDiagnosticError(
                code=CODE_SEMANTIC,
                path="/rules",
                message="rules must be an array",
                source=source,
            )
        ]

    normalized: list[tuple[AuthorityRuleKey, AuthorityDeclaration]] = []
    errors: list[AuthorityDiagnosticError] = []
    for index, item in enumerate(rules_raw):
        pointer = f"/rules/{index}"
        if not isinstance(item, dict):
            errors.append(
                AuthorityDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=pointer,
                    message="rule must be a mapping",
                    source=source,
                )
            )
            continue
        try:
            normalized.append(_normalize_rule(item, pointer=pointer, source=source))
        except AuthoritySemanticError as exc:
            errors.extend(exc.errors)
    return normalized, errors


def _normalize_rule(
    item: dict[str, Any],
    *,
    pointer: str,
    source: str,
) -> tuple[AuthorityRuleKey, AuthorityDeclaration]:
    rule_id = item.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise AuthoritySemanticError(
            [
                AuthorityDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/id",
                    message="authority rule id is required",
                    source=source,
                )
            ]
        )

    description = item.get("description")
    if description is not None:
        if not isinstance(description, str) or not description.strip():
            raise AuthoritySemanticError(
                [
                    AuthorityDiagnosticError(
                        code=CODE_SEMANTIC,
                        path=f"{pointer}/description",
                        message="description must be a non-empty string when provided",
                        source=source,
                    )
                ]
            )
        description = description.strip()

    select_raw = item.get("select")
    if not isinstance(select_raw, dict):
        raise AuthoritySemanticError(
            [
                AuthorityDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/select",
                    message="select must be a mapping",
                    source=source,
                )
            ]
        )
    selector = _normalize_selector(
        select_raw,
        pointer=f"{pointer}/select",
        source=source,
    )

    authority_raw = item.get("authority")
    if not isinstance(authority_raw, dict):
        raise AuthoritySemanticError(
            [
                AuthorityDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/authority",
                    message="authority must be a mapping",
                    source=source,
                )
            ]
        )
    target = _normalize_target(
        authority_raw,
        pointer=f"{pointer}/authority",
        source=source,
    )

    key = AuthorityRuleKey(selector=selector, authority=target)
    declaration = AuthorityDeclaration(config_id=rule_id.strip(), description=description)
    return key, declaration


def _normalize_selector(
    select_raw: dict[str, Any],
    *,
    pointer: str,
    source: str,
) -> AuthoritySelector:
    kind = select_raw.get("kind")
    if kind not in _AUTHORITY_KIND_SET:
        raise AuthoritySemanticError(
            [
                AuthorityDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/kind",
                    message="unsupported object kind",
                    source=source,
                )
            ]
        )

    property_raw = select_raw.get("property")
    if not isinstance(property_raw, str):
        raise AuthoritySemanticError(
            [
                AuthorityDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/property",
                    message="property must be a JSON Pointer string",
                    source=source,
                )
            ]
        )
    try:
        property_path = PropertyPath.parse(property_raw)
    except (TypeError, ValueError) as exc:
        raise AuthoritySemanticError(
            [
                AuthorityDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/property",
                    message="property must be a valid RFC6901 JSON Pointer",
                    source=source,
                )
            ]
        ) from exc

    namespace = select_raw.get("namespace")
    if namespace is not None:
        if not isinstance(namespace, str) or not namespace.strip():
            raise AuthoritySemanticError(
                [
                    AuthorityDiagnosticError(
                        code=CODE_SEMANTIC,
                        path=f"{pointer}/namespace",
                        message="namespace must be a non-empty string when provided",
                        source=source,
                    )
                ]
            )
        namespace = namespace.strip()

    return AuthoritySelector(kind=kind, property_path=property_path, namespace=namespace)


def _normalize_target(
    authority_raw: dict[str, Any],
    *,
    pointer: str,
    source: str,
) -> AuthorityTarget:
    provider_type = authority_raw.get("provider_type")
    if not isinstance(provider_type, str) or not provider_type.strip():
        raise AuthoritySemanticError(
            [
                AuthorityDiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/provider_type",
                    message="provider_type must be a non-empty string",
                    source=source,
                )
            ]
        )

    source_ref = authority_raw.get("source_ref")
    if source_ref is not None:
        if not isinstance(source_ref, str) or not source_ref.strip():
            raise AuthoritySemanticError(
                [
                    AuthorityDiagnosticError(
                        code=CODE_SEMANTIC,
                        path=f"{pointer}/source_ref",
                        message="source_ref must be a non-empty string when provided",
                        source=source,
                    )
                ]
            )
        source_ref = source_ref.strip()

    return AuthorityTarget(provider_type=provider_type.strip(), source_ref=source_ref)
