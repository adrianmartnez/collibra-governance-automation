"""Persist and load governance-property-observations v1 artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import jsonschema
from jsonschema import Draft202012Validator

from governance.domain.graph import GraphNodeIdentity, ProvenanceRecord
from governance.domain.observations import (
    PROPERTY_OBSERVATION_SET_SCHEMA,
    PROPERTY_OBSERVATION_SET_VERSION,
    PropertyObservation,
    PropertyObservationSet,
    PropertyPath,
)
from governance.identity.hashing import ContentIdentity, property_observation_set_identity
from governance.identity.json_values import validate_json_value
from governance.io.atomic import atomic_write_text

ObservationsArtifactErrorCode = Literal[
    "read_error",
    "parse_error",
    "invalid_artifact",
    "unsupported_schema",
    "unsupported_version",
    "integrity_mismatch",
    "write_error",
]

_OBSERVATION_MODES = frozenset({"declared", "observed", "derived"})
_IDENTITY_KEYS = frozenset({"namespace", "kind", "logical_id", "parent"})
_PROVENANCE_KEYS = frozenset({"provider_type", "source_ref", "source_version", "observation_mode"})
_OBSERVATION_ENTRY_KEYS = frozenset({"object", "property", "provenance", "value"})
_CONTENT_IDENTITY_KEYS = frozenset({"algorithm", "hashing_contract_version", "digest"})


@dataclass(frozen=True, slots=True)
class ObservationsArtifactDiagnostic:
    code: ObservationsArtifactErrorCode
    path: str
    message: str


class ObservationsArtifactError(RuntimeError):
    """Neutral property-observations artifact load/validation failure."""

    def __init__(self, errors: list[ObservationsArtifactDiagnostic]) -> None:
        if not errors:
            raise ValueError("ObservationsArtifactError requires at least one diagnostic")
        self.errors = list(errors)
        super().__init__(errors[0].message)


def property_observation_set_to_dict(observation_set: PropertyObservationSet) -> dict[str, Any]:
    """Build the persisted artifact payload (identity body + content_identity)."""
    if not isinstance(observation_set, PropertyObservationSet):
        raise TypeError("observation_set must be PropertyObservationSet")
    payload = observation_set.to_identity_dict()
    payload["content_identity"] = observation_set.content_identity().to_dict()
    return payload


def property_observation_set_to_json(observation_set: PropertyObservationSet) -> str:
    """Canonical JSON text for a property observation set artifact."""
    return (
        json.dumps(
            property_observation_set_to_dict(observation_set),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n"
    )


def write_property_observation_set(
    observation_set: PropertyObservationSet,
    path: str | Path,
) -> Path:
    """Atomically write a governance-property-observations v1 artifact."""
    target = Path(path)
    try:
        return atomic_write_text(target, property_observation_set_to_json(observation_set))
    except OSError as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="write_error",
                    path="/output",
                    message="unable to write observations artifact",
                )
            ]
        ) from exc


def load_property_observation_set_artifact(path: str | Path) -> PropertyObservationSet:
    """Load, schema-validate, and verify a property observations v1 artifact."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="parse_error",
                    path="/",
                    message="invalid observations JSON",
                )
            ]
        ) from exc
    except OSError as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="read_error",
                    path="/",
                    message="unable to read observations artifact",
                )
            ]
        ) from exc

    def _reject_non_standard_json(_value: str) -> None:
        raise ValueError("non-standard JSON literal")

    def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            text,
            parse_constant=_reject_non_standard_json,
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="parse_error",
                    path="/",
                    message="invalid observations JSON",
                )
            ]
        ) from exc

    try:
        validate_json_value(payload)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="parse_error",
                    path="/",
                    message="invalid observations JSON",
                )
            ]
        ) from exc

    if not isinstance(payload, dict):
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="parse_error",
                    path="/",
                    message="observations root must be a mapping",
                )
            ]
        )

    schema_name = payload.get("observation_schema")
    version = payload.get("observation_version")
    if schema_name != PROPERTY_OBSERVATION_SET_SCHEMA:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="unsupported_schema",
                    path="/observation_schema",
                    message="unsupported observations schema",
                )
            ]
        )
    if version != PROPERTY_OBSERVATION_SET_VERSION:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="unsupported_version",
                    path="/observation_version",
                    message="unsupported observations version",
                )
            ]
        )

    schema_text = (
        files("governance.observations.schemas")
        .joinpath("governance-property-observations.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    validator = Draft202012Validator(schema)
    try:
        schema_errors = sorted(
            validator.iter_errors(payload),
            key=lambda item: (
                "/" + "/".join(str(part) for part in item.absolute_path),
                item.validator,
            ),
        )
    except RecursionError as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/",
                    message="observations artifact is too deeply nested",
                )
            ]
        ) from exc
    if schema_errors:
        diagnostics = [_schema_diagnostic(error) for error in schema_errors]
        raise ObservationsArtifactError(diagnostics)

    identity_raw = payload.get("content_identity")
    stored_observations = payload.get("observations")
    if not isinstance(identity_raw, dict):
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/content_identity",
                    message="observations content_identity is required",
                )
            ]
        )
    if not isinstance(stored_observations, list):
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/observations",
                    message="observations must be an array",
                )
            ]
        )

    parsed: list[PropertyObservation] = []
    for index, item in enumerate(stored_observations):
        parsed.append(_parse_observation_entry(item, path=f"/observations/{index}"))

    try:
        observation_set = PropertyObservationSet.from_observations(parsed)
    except (TypeError, ValueError) as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/observations",
                    message="invalid property observation set",
                )
            ]
        ) from exc

    normalized = [entry.to_identity_entry() for entry in observation_set.observations]
    if normalized != stored_observations:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/observations",
                    message="observations are not in domain-normalized order",
                )
            ]
        )

    expected = property_observation_set_identity(observation_set.to_identity_dict())
    actual = _parse_content_identity(identity_raw, path="/content_identity")
    if actual != expected:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="integrity_mismatch",
                    path="/content_identity",
                    message="observations content_identity mismatch",
                )
            ]
        )

    return observation_set


def _schema_diagnostic(error: jsonschema.ValidationError) -> ObservationsArtifactDiagnostic:
    path_parts = [str(part) for part in error.absolute_path]
    logical_path = "/" + "/".join(path_parts) if path_parts else "/"
    keyword = error.validator
    return ObservationsArtifactDiagnostic(
        code="invalid_artifact",
        path=logical_path,
        message=f"observations artifact failed schema validation ({keyword})",
    )


def _require_exact_non_empty_str(value: Any, *, path: str, message: str) -> str:
    if not isinstance(value, str):
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message=message,
                )
            ]
        )
    if not value or value.strip() != value:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message=message,
                )
            ]
        )
    return value


def _parse_content_identity(raw: dict[str, Any], *, path: str) -> ContentIdentity:
    if set(raw) != _CONTENT_IDENTITY_KEYS:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid observations content_identity",
                )
            ]
        )
    algorithm = raw["algorithm"]
    hashing_contract_version = raw["hashing_contract_version"]
    digest = raw["digest"]
    if (
        not isinstance(algorithm, str)
        or not isinstance(hashing_contract_version, str)
        or not isinstance(digest, str)
    ):
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid observations content_identity",
                )
            ]
        )
    return ContentIdentity(
        algorithm=algorithm,
        hashing_contract_version=hashing_contract_version,
        digest=digest,
    )


def _parse_graph_node_identity(raw: Any, *, path: str) -> GraphNodeIdentity:
    if not isinstance(raw, dict):
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid graph node identity",
                )
            ]
        )
    if set(raw) != _IDENTITY_KEYS:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid graph node identity",
                )
            ]
        )
    namespace = _require_exact_non_empty_str(
        raw["namespace"],
        path=f"{path}/namespace",
        message="invalid graph node identity",
    )
    kind = _require_exact_non_empty_str(
        raw["kind"],
        path=f"{path}/kind",
        message="invalid graph node identity",
    )
    logical_id = _require_exact_non_empty_str(
        raw["logical_id"],
        path=f"{path}/logical_id",
        message="invalid graph node identity",
    )
    parent_raw = raw["parent"]
    parent: GraphNodeIdentity | None
    if parent_raw is None:
        parent = None
    else:
        parent = _parse_graph_node_identity(parent_raw, path=f"{path}/parent")
    try:
        return GraphNodeIdentity(
            namespace=namespace,
            kind=kind,
            logical_id=logical_id,
            parent=parent,
        )
    except (TypeError, ValueError) as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid graph node identity",
                )
            ]
        ) from exc


def _parse_provenance_record(raw: Any, *, path: str) -> ProvenanceRecord:
    if not isinstance(raw, dict):
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid provenance record",
                )
            ]
        )
    if set(raw) != _PROVENANCE_KEYS:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid provenance record",
                )
            ]
        )
    provider_type = _require_exact_non_empty_str(
        raw["provider_type"],
        path=f"{path}/provider_type",
        message="invalid provenance record",
    )
    source_ref = _require_exact_non_empty_str(
        raw["source_ref"],
        path=f"{path}/source_ref",
        message="invalid provenance record",
    )
    source_version_raw = raw["source_version"]
    source_version: str | None
    if source_version_raw is None:
        source_version = None
    else:
        source_version = _require_exact_non_empty_str(
            source_version_raw,
            path=f"{path}/source_version",
            message="invalid provenance record",
        )
    observation_mode = _require_exact_non_empty_str(
        raw["observation_mode"],
        path=f"{path}/observation_mode",
        message="invalid provenance record",
    )
    if observation_mode not in _OBSERVATION_MODES:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=f"{path}/observation_mode",
                    message="invalid provenance record",
                )
            ]
        )
    try:
        return ProvenanceRecord(
            provider_type=provider_type,
            source_ref=source_ref,
            source_version=source_version,
            observation_mode=observation_mode,
        )
    except (TypeError, ValueError) as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid provenance record",
                )
            ]
        ) from exc


def _parse_property_path(raw: Any, *, path: str) -> PropertyPath:
    if not isinstance(raw, str):
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid property pointer",
                )
            ]
        )
    try:
        parsed = PropertyPath.parse(raw)
    except (TypeError, ValueError) as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid property pointer",
                )
            ]
        ) from exc
    if parsed.to_pointer() != raw:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="property pointer is not canonical",
                )
            ]
        )
    return parsed


def _parse_observation_entry(raw: Any, *, path: str) -> PropertyObservation:
    if not isinstance(raw, dict):
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid property observation entry",
                )
            ]
        )
    if set(raw) != _OBSERVATION_ENTRY_KEYS:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid property observation entry",
                )
            ]
        )
    object_identity = _parse_graph_node_identity(raw["object"], path=f"{path}/object")
    property_path = _parse_property_path(raw["property"], path=f"{path}/property")
    provenance_raw = raw["provenance"]
    if not isinstance(provenance_raw, list) or not provenance_raw:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=f"{path}/provenance",
                    message="invalid property observation provenance",
                )
            ]
        )
    provenance = tuple(
        _parse_provenance_record(item, path=f"{path}/provenance/{index}")
        for index, item in enumerate(provenance_raw)
    )
    value = raw["value"]
    try:
        validate_json_value(value)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=f"{path}/value",
                    message="invalid property observation value",
                )
            ]
        ) from exc
    try:
        return PropertyObservation(
            object_identity=object_identity,
            property_path=property_path,
            value=value,
            provenance=provenance,
        )
    except (TypeError, ValueError) as exc:
        raise ObservationsArtifactError(
            [
                ObservationsArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid property observation entry",
                )
            ]
        ) from exc


__all__ = [
    "ObservationsArtifactDiagnostic",
    "ObservationsArtifactError",
    "ObservationsArtifactErrorCode",
    "load_property_observation_set_artifact",
    "property_observation_set_to_dict",
    "property_observation_set_to_json",
    "write_property_observation_set",
]
