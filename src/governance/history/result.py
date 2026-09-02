"""Build governance-history-evolution v1 artifacts."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

from governance.comparison.projection import ComparisonObjectIdentity
from governance.domain.graph import GraphNodeIdentity
from governance.domain.observations import PropertyPath
from governance.history.errors import (
    CODE_INVALID_HISTORY_ARTIFACT,
    CODE_OBJECT_NOT_FOUND,
    CODE_PROPERTY_NOT_FOUND,
    DiagnosticError,
    HistoryError,
)
from governance.history.evolution import (
    build_transition,
    object_known_in_context,
    snapshot_property_state,
)
from governance.history.load import ResolvedHistory
from governance.history.models import EVOLUTION_SCHEMA, EVOLUTION_VERSION
from governance.identity.hashing import history_evolution_identity

_EVOLUTION_SCHEMA_RESOURCE = "governance-history-evolution.v1.schema.json"
_evolution_validator: Draft202012Validator | None = None


def _load_evolution_schema() -> dict[str, Any]:
    text = (
        files("governance.history.schemas")
        .joinpath(_EVOLUTION_SCHEMA_RESOURCE)
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def _get_evolution_validator() -> Draft202012Validator:
    global _evolution_validator
    if _evolution_validator is None:
        _evolution_validator = Draft202012Validator(_load_evolution_schema())
    return _evolution_validator


def validate_evolution_result_against_schema(result: dict[str, Any]) -> None:
    """Validate a built evolution payload against the production JSON Schema."""
    validator = _get_evolution_validator()
    try:
        error = next(validator.iter_errors(result), None)
    except RecursionError as exc:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path="/",
                    message="evolution artifact is too deeply nested",
                )
            ]
        ) from exc
    if error is None:
        return
    path_parts = [str(part) for part in error.absolute_path]
    logical_path = "/" + "/".join(path_parts) if path_parts else "/"
    raise HistoryError(
        [
            DiagnosticError(
                code=CODE_INVALID_HISTORY_ARTIFACT,
                path=logical_path,
                message=f"evolution artifact failed schema validation ({error.validator})",
            )
        ]
    )


def _object_present_somewhere(
    resolved: ResolvedHistory,
    identity: ComparisonObjectIdentity,
) -> bool:
    return any(identity in state.projected.objects for state in resolved.states)


def _property_present_somewhere(
    resolved: ResolvedHistory,
    identity: ComparisonObjectIdentity,
    property_path: str,
) -> bool:
    for state in resolved.states:
        if identity not in state.projected.objects:
            continue
        value = snapshot_property_state(state.projected, identity, property_path)
        if value.get("has_value"):
            return True
        obj = state.projected.objects[identity]
        if property_path in obj.properties:
            return True
    return False


def _governance_object_present_somewhere(
    resolved: ResolvedHistory,
    identity: GraphNodeIdentity,
) -> bool:
    return any(object_known_in_context(state, identity) for state in resolved.states)


def _governance_property_present_somewhere(
    resolved: ResolvedHistory,
    identity: GraphNodeIdentity,
    property_path: str,
) -> bool:
    parsed = PropertyPath.parse(property_path)
    for state in resolved.states:
        if state.observations is not None:
            for item in state.observations.observations:
                if item.object_identity == identity and item.property_path == parsed:
                    return True
        if state.conflicts is not None:
            for item in state.conflicts.results:
                if item.object_identity == identity and item.property_path == parsed:
                    return True
    return False


def build_history_evolution(
    resolved: ResolvedHistory,
    *,
    queried_object: ComparisonObjectIdentity | None = None,
    queried_governance_object: GraphNodeIdentity | None = None,
    queried_property: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic governance-history-evolution v1 result."""
    if queried_object is None and queried_governance_object is None:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path="/",
                    message=(
                        "at least one of queried_object or queried_governance_object is required"
                    ),
                )
            ]
        )

    property_pointer: str | None = None
    if queried_property is not None:
        parsed = PropertyPath.parse(queried_property)
        property_pointer = parsed.to_pointer()
        if property_pointer != queried_property:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path="/queried_property",
                        message="property pointer is not canonical",
                    )
                ]
            )

    if queried_object is not None and not _object_present_somewhere(resolved, queried_object):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_OBJECT_NOT_FOUND,
                    path="/queried_object",
                    message="queried object not found in history",
                )
            ]
        )
    if (
        queried_object is not None
        and property_pointer is not None
        and not _property_present_somewhere(resolved, queried_object, property_pointer)
    ):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_PROPERTY_NOT_FOUND,
                    path="/queried_property",
                    message="queried property not found for object in history",
                )
            ]
        )

    if queried_governance_object is not None and not _governance_object_present_somewhere(
        resolved, queried_governance_object
    ):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_OBJECT_NOT_FOUND,
                    path="/queried_governance_object",
                    message="queried governance object not found in history context",
                )
            ]
        )
    if (
        queried_governance_object is not None
        and property_pointer is not None
        and not _governance_property_present_somewhere(
            resolved, queried_governance_object, property_pointer
        )
    ):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_PROPERTY_NOT_FOUND,
                    path="/queried_property",
                    message=("queried property not found for governance object in history context"),
                )
            ]
        )

    transitions: list[dict[str, Any]] = []
    for index, comparison in enumerate(resolved.comparisons):
        baseline = resolved.states[index]
        candidate = resolved.states[index + 1]
        transitions.append(
            build_transition(
                from_index=index,
                to_index=index + 1,
                baseline=baseline,
                candidate=candidate,
                comparison=comparison,
                queried_object=queried_object,
                queried_governance_object=queried_governance_object,
                queried_property=property_pointer,
            )
        )

    without_identity: dict[str, Any] = {
        "comparison_policy": resolved.history.comparison_policy.to_dict(),
        "evolution_schema": EVOLUTION_SCHEMA,
        "evolution_version": EVOLUTION_VERSION,
        "history_content_identity": resolved.history.content_identity().to_dict(),
        "queried_governance_object": (
            None if queried_governance_object is None else queried_governance_object.to_dict()
        ),
        "queried_object": None if queried_object is None else queried_object.to_dict(),
        "queried_property": property_pointer,
        "transitions": transitions,
        "writes_performed": 0,
    }
    identity = history_evolution_identity(without_identity)
    result = {**without_identity, "content_identity": identity.to_dict()}
    validate_evolution_result_against_schema(result)
    return result


__all__ = ["build_history_evolution", "validate_evolution_result_against_schema"]
