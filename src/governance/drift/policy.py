"""Parse, validate, and normalize governance drift policy files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

from governance.comparison.compare import comparable_values_equal
from governance.comparison.projection import ComparisonObjectIdentity, GovernedObjectKind
from governance.comparison.properties import (
    comparable_change_value_compatible,
    comparison_change_property_compatible_with_kind,
)
from governance.domain.observations import PropertyPath
from governance.drift.errors import (
    CODE_AMBIGUOUS_DRIFT_POLICY,
    CODE_INVALID_POLICY,
    DiagnosticError,
    DriftError,
)
from governance.drift.models import (
    DRIFT_POLICY_SCHEMA,
    DRIFT_POLICY_VERSION,
    NormalizedDriftPolicy,
    NormalizedDriftRule,
)
from governance.drift.pointer import join_pointer
from governance.identity.json_values import normalize_json_value, validate_json_value

DriftChange = str
_CREDENTIAL_KEYS = frozenset({"password", "token", "client_secret"})


@dataclass(frozen=True, slots=True)
class SelectorGroup:
    selector_key: tuple[Any, ...]
    rules: tuple[NormalizedDriftRule, ...]
    expectation_projections: frozenset[str]


def parse_and_normalize_policy(document: Any) -> NormalizedDriftPolicy:
    errors: list[DiagnosticError] = []
    if not isinstance(document, dict):
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path="/",
                    message="drift policy root must be a mapping",
                )
            ]
        )
    _validate_policy_schema(document)
    _reject_extra_keys(document, path="/", allowed={"drift_schema", "drift_version", "rules"})
    if document.get("drift_schema") != DRIFT_POLICY_SCHEMA:
        errors.append(
            DiagnosticError(
                code=CODE_INVALID_POLICY,
                path="/drift_schema",
                message="unsupported drift policy schema",
            )
        )
    if document.get("drift_version") != DRIFT_POLICY_VERSION:
        errors.append(
            DiagnosticError(
                code=CODE_INVALID_POLICY,
                path="/drift_version",
                message="unsupported drift policy version",
            )
        )
    rules_raw = document.get("rules")
    if not isinstance(rules_raw, list):
        errors.append(
            DiagnosticError(
                code=CODE_INVALID_POLICY,
                path="/rules",
                message="drift policy rules must be an array",
            )
        )
        raise DriftError(errors)

    normalized_rules: list[NormalizedDriftRule] = []
    seen_ids: set[str] = set()
    for index, rule_raw in enumerate(rules_raw):
        rule_path = f"/rules/{index}"
        try:
            normalized = _normalize_rule(rule_raw, path=rule_path)
        except DriftError as exc:
            errors.extend(exc.errors)
            continue
        if normalized.id in seen_ids:
            errors.append(
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=f"{rule_path}/id",
                    message="duplicate drift policy rule id",
                )
            )
            continue
        seen_ids.add(normalized.id)
        normalized_rules.append(normalized)

    if errors:
        raise DriftError(errors)

    policy = NormalizedDriftPolicy(rules=tuple(normalized_rules))
    _validate_selector_ambiguity(policy)
    return policy


def _validate_selector_ambiguity(policy: NormalizedDriftPolicy) -> None:
    groups: dict[tuple[Any, ...], list[NormalizedDriftRule]] = {}
    for rule in policy.rules:
        groups.setdefault(rule.selector_key(), []).append(rule)

    errors: list[DiagnosticError] = []
    for _selector_key, rules in sorted(groups.items(), key=lambda item: item[0]):
        projections = {rule.expectation_projection() for rule in rules}
        if len(projections) > 1:
            path = "/rules"
            errors.append(
                DiagnosticError(
                    code=CODE_AMBIGUOUS_DRIFT_POLICY,
                    path=path,
                    message="conflicting expectations for the same drift policy selector",
                )
            )
    if errors:
        raise DriftError(errors)


def _normalize_rule(rule_raw: Any, *, path: str) -> NormalizedDriftRule:
    if not isinstance(rule_raw, dict):
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=path,
                    message="drift policy rule must be a mapping",
                )
            ]
        )
    _reject_extra_keys(rule_raw, path=path, allowed={"id", "match", "expected"})
    rule_id = rule_raw.get("id")
    if not isinstance(rule_id, str):
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=f"{path}/id",
                    message="drift policy rule id must be a string",
                )
            ]
        )
    normalized_id = rule_id.strip()
    if not normalized_id:
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=f"{path}/id",
                    message="drift policy rule id must be non-empty",
                )
            ]
        )

    match_raw = rule_raw.get("match")
    if not isinstance(match_raw, dict):
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=f"{path}/match",
                    message="drift policy rule match must be a mapping",
                )
            ]
        )
    _reject_extra_keys(match_raw, path=f"{path}/match", allowed={"change", "object", "property"})

    change = match_raw.get("change")
    if change not in {"added", "removed", "changed"}:
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=f"{path}/match/change",
                    message="drift policy change must be added, removed, or changed",
                )
            ]
        )

    object_raw = match_raw.get("object")
    if not isinstance(object_raw, dict):
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=f"{path}/match/object",
                    message="drift policy object selector must be a mapping",
                )
            ]
        )
    _reject_extra_keys(object_raw, path=f"{path}/match/object", allowed={"kind", "path"})
    try:
        identity = ComparisonObjectIdentity(
            kind=object_raw["kind"],
            path=tuple(object_raw["path"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=f"{path}/match/object",
                    message="invalid drift policy object selector",
                )
            ]
        ) from exc
    object_identity = identity.to_dict()
    kind: GovernedObjectKind = identity.kind

    if change in {"added", "removed"} and kind in {"data_source", "database"}:
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=f"{path}/match/object",
                    message="root object add/remove rules are not supported",
                )
            ]
        )

    property_pointer: str | None = None
    if "property" in match_raw:
        property_pointer = match_raw.get("property")
        if change != "changed":
            raise DriftError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_POLICY,
                        path=f"{path}/match/property",
                        message="property selector requires change changed",
                    )
                ]
            )
        if not isinstance(property_pointer, str):
            raise DriftError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_POLICY,
                        path=f"{path}/match/property",
                        message="property selector must be a string",
                    )
                ]
            )
        try:
            parsed = PropertyPath.parse(property_pointer)
        except (TypeError, ValueError) as exc:
            raise DriftError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_POLICY,
                        path=f"{path}/match/property",
                        message="invalid property pointer",
                    )
                ]
            ) from exc
        if parsed.to_pointer() != property_pointer:
            raise DriftError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_POLICY,
                        path=f"{path}/match/property",
                        message="property pointer is not canonical",
                    )
                ]
            )
        if not comparison_change_property_compatible_with_kind(kind, property_pointer):
            raise DriftError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_POLICY,
                        path=f"{path}/match/property",
                        message="property is not compatible with object kind",
                    )
                ]
            )
    elif change == "changed":
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=f"{path}/match/property",
                    message="changed drift policy rule requires property selector",
                )
            ]
        )

    expected_raw = rule_raw.get("expected")
    expected: dict[str, Any] | None = None
    if expected_raw is not None:
        if change != "changed" or property_pointer is None:
            raise DriftError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_POLICY,
                        path=f"{path}/expected",
                        message="expected constraints require change changed with property",
                    )
                ]
            )
        if not isinstance(expected_raw, dict):
            raise DriftError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_POLICY,
                        path=f"{path}/expected",
                        message="expected block must be a mapping",
                    )
                ]
            )
        _reject_extra_keys(expected_raw, path=f"{path}/expected", allowed={"baseline", "candidate"})
        if not expected_raw:
            raise DriftError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_POLICY,
                        path=f"{path}/expected",
                        message="expected block must not be empty",
                    )
                ]
            )
        expected = {}
        if "baseline" in expected_raw:
            expected["baseline"] = _normalize_comparable_value(
                expected_raw["baseline"],
                path=f"{path}/expected/baseline",
            )
        if "candidate" in expected_raw:
            expected["candidate"] = _normalize_comparable_value(
                expected_raw["candidate"],
                path=f"{path}/expected/candidate",
            )
        for side_name, side_value in expected.items():
            if not comparable_change_value_compatible(identity, property_pointer, side_value):
                raise DriftError(
                    [
                        DiagnosticError(
                            code=CODE_INVALID_POLICY,
                            path=f"{path}/expected/{side_name}",
                            message=(
                                "expected value is not compatible with comparison producer contract"
                            ),
                        )
                    ]
                )
        if (
            "baseline" in expected
            and "candidate" in expected
            and _comparable_sides_equal(expected["baseline"], expected["candidate"])
        ):
            raise DriftError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_POLICY,
                        path=f"{path}/expected",
                        message="expected baseline and candidate must not be equal",
                    )
                ]
            )

    return NormalizedDriftRule(
        id=normalized_id,
        change=change,
        object_identity=object_identity,
        property=property_pointer,
        expected=expected,
    )


def _comparable_sides_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("has_value") != right.get("has_value"):
        return False
    if not left.get("has_value"):
        return True
    return comparable_values_equal(left["value"], right["value"])


def _normalize_comparable_value(raw: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=path,
                    message="comparable property value must be a mapping",
                )
            ]
        )
    _reject_extra_keys(raw, path=path, allowed={"has_value", "value"})
    has_value = raw.get("has_value")
    if has_value is True:
        if "value" not in raw:
            raise DriftError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_POLICY,
                        path=path,
                        message="comparable property value requires value when has_value is true",
                    )
                ]
            )
        try:
            validate_json_value(raw["value"])
            value = normalize_json_value(raw["value"])
        except (TypeError, ValueError, RecursionError) as exc:
            raise DriftError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_POLICY,
                        path=f"{path}/value",
                        message="expected value must be JSON-compatible",
                    )
                ]
            ) from exc
        return {"has_value": True, "value": value}
    if has_value is False and "value" not in raw:
        return {"has_value": False}
    raise DriftError(
        [
            DiagnosticError(
                code=CODE_INVALID_POLICY,
                path=path,
                message="invalid comparable property value",
            )
        ]
    )


def _validate_policy_schema(document: dict[str, Any]) -> None:
    schema_text = (
        files("governance.drift.schemas")
        .joinpath("governance-drift-policy.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    validator = Draft202012Validator(schema)
    schema_errors = sorted(
        validator.iter_errors(document),
        key=lambda item: (_schema_error_path(item), item.validator),
    )
    if schema_errors:
        raise DriftError([_schema_diagnostic(error) for error in schema_errors])


def _schema_error_path(error: jsonschema.ValidationError) -> str:
    parts = [str(part) for part in error.absolute_path]
    if not parts:
        return "/"
    path = "/"
    for part in parts:
        path = join_pointer(path, part)
    return path


def _schema_diagnostic(error: jsonschema.ValidationError) -> DiagnosticError:
    keyword = error.validator
    return DiagnosticError(
        code=CODE_INVALID_POLICY,
        path=_schema_error_path(error),
        message=f"drift policy failed schema validation ({keyword})",
    )


def _reject_extra_keys(raw: dict[str, Any], *, path: str, allowed: set[str]) -> None:
    extra = set(raw) - allowed
    if extra:
        key = sorted(str(item) for item in extra)[0]
        if key in _CREDENTIAL_KEYS:
            message = "credential-shaped key is not allowed in drift policy"
        else:
            message = "unexpected field in drift policy"
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path=join_pointer(path, key),
                    message=message,
                )
            ]
        )


def build_policy_index(
    policy: NormalizedDriftPolicy,
) -> dict[tuple[Any, ...], tuple[NormalizedDriftRule, ...]]:
    groups: dict[tuple[Any, ...], list[NormalizedDriftRule]] = {}
    for rule in policy.rules:
        groups.setdefault(rule.selector_key(), []).append(rule)
    return {key: tuple(rules) for key, rules in groups.items()}


def expectation_projection_for_rules(rules: tuple[NormalizedDriftRule, ...]) -> str:
    projections = {rule.expectation_projection() for rule in rules}
    if len(projections) != 1:
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_AMBIGUOUS_DRIFT_POLICY,
                    path="/rules",
                    message="ambiguous drift policy expectations",
                )
            ]
        )
    return next(iter(projections))
