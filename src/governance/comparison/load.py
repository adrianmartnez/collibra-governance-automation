"""Load and validate persisted governance-snapshot-comparison v1 artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import jsonschema
from jsonschema import Draft202012Validator

from governance.comparison.compare import CHANGE_RANK
from governance.comparison.projection import ComparisonObjectIdentity
from governance.comparison.properties import (
    comparable_change_value_compatible,
    comparison_change_property_compatible_with_kind,
    foreign_key_reference_sides_coherent,
)
from governance.comparison.result import COMPARISON_SCHEMA, COMPARISON_VERSION, DIRECTION
from governance.domain.observations import PropertyPath
from governance.exporters.inventory import SCANNER_CONTRACT_VERSION
from governance.identity.hashing import ContentIdentity, snapshot_comparison_identity
from governance.identity.json_values import canonical_value_fingerprint, validate_json_value

ComparisonArtifactErrorCode = Literal[
    "read_error",
    "parse_error",
    "invalid_artifact",
    "unsupported_schema",
    "unsupported_version",
    "integrity_mismatch",
]


@dataclass(frozen=True, slots=True)
class ComparisonArtifactDiagnostic:
    code: ComparisonArtifactErrorCode
    path: str
    message: str


class ComparisonArtifactError(RuntimeError):
    """Neutral comparison artifact load/validation failure."""

    def __init__(self, errors: list[ComparisonArtifactDiagnostic]) -> None:
        if not errors:
            raise ValueError("ComparisonArtifactError requires at least one diagnostic")
        self.errors = list(errors)
        super().__init__(errors[0].message)


def load_comparison_artifact(path: str | Path) -> dict[str, Any]:
    """Load, schema-validate, and verify a persisted comparison v1 artifact."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="parse_error",
                    path="/",
                    message="invalid comparison JSON",
                )
            ]
        ) from exc
    except OSError as exc:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="read_error",
                    path="/",
                    message="unable to read comparison artifact",
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
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="parse_error",
                    path="/",
                    message="invalid comparison JSON",
                )
            ]
        ) from exc

    try:
        validate_json_value(payload)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="parse_error",
                    path="/",
                    message="invalid comparison JSON",
                )
            ]
        ) from exc

    if not isinstance(payload, dict):
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="parse_error",
                    path="/",
                    message="comparison root must be a mapping",
                )
            ]
        )

    schema_name = payload.get("comparison_schema")
    version = payload.get("comparison_version")
    if schema_name != COMPARISON_SCHEMA:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="unsupported_schema",
                    path="/comparison_schema",
                    message="unsupported comparison schema",
                )
            ]
        )
    if version != COMPARISON_VERSION:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="unsupported_version",
                    path="/comparison_version",
                    message="unsupported comparison version",
                )
            ]
        )

    schema_text = (
        files("governance.comparison.schemas")
        .joinpath("governance-snapshot-comparison.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    validator = Draft202012Validator(schema)
    schema_errors = sorted(
        validator.iter_errors(payload),
        key=lambda item: ("/" + "/".join(str(part) for part in item.absolute_path), item.validator),
    )
    if schema_errors:
        diagnostics = [_schema_diagnostic(error) for error in schema_errors]
        raise ComparisonArtifactError(diagnostics)

    identity_raw = payload.get("content_identity")
    if not isinstance(identity_raw, dict):
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/content_identity",
                    message="comparison content_identity is required",
                )
            ]
        )

    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    expected = snapshot_comparison_identity(without_identity)
    actual = ContentIdentity(
        algorithm=str(identity_raw.get("algorithm", "")),
        hashing_contract_version=str(identity_raw.get("hashing_contract_version", "")),
        digest=str(identity_raw.get("digest", "")),
    )
    if actual != expected:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="integrity_mismatch",
                    path="/content_identity",
                    message="comparison content_identity mismatch",
                )
            ]
        )

    validate_comparison_result_semantics(payload)
    return payload


def _schema_diagnostic(error: jsonschema.ValidationError) -> ComparisonArtifactDiagnostic:
    path_parts = [str(part) for part in error.absolute_path]
    logical_path = "/" + "/".join(path_parts) if path_parts else "/"
    keyword = error.validator
    return ComparisonArtifactDiagnostic(
        code="invalid_artifact",
        path=logical_path,
        message=f"comparison artifact failed schema validation ({keyword})",
    )


def _parse_identity(raw: Any, *, path: str) -> ComparisonObjectIdentity:
    if not isinstance(raw, dict):
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid comparison object identity",
                )
            ]
        )
    if set(raw) != {"kind", "path"}:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid comparison object identity",
                )
            ]
        )
    raw_path = raw["path"]
    if not isinstance(raw_path, list):
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid comparison object identity",
                )
            ]
        )
    try:
        return ComparisonObjectIdentity(kind=raw["kind"], path=tuple(raw_path))
    except (TypeError, ValueError) as exc:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid comparison object identity",
                )
            ]
        ) from exc


def _expected_parent(identity: ComparisonObjectIdentity) -> ComparisonObjectIdentity | None:
    kind = identity.kind
    path = identity.path
    if kind == "data_source":
        return None
    if kind == "database":
        return ComparisonObjectIdentity("data_source", ())
    if kind == "schema":
        return ComparisonObjectIdentity("database", ())
    if kind == "table":
        return ComparisonObjectIdentity("schema", (path[0],))
    if kind in ("column", "primary_key", "foreign_key", "relationship"):
        return ComparisonObjectIdentity("table", (path[0], path[1]))
    raise ValueError(f"unsupported kind: {kind!r}")


def _parent_matches(
    identity: ComparisonObjectIdentity,
    parent_raw: dict[str, Any] | None,
    *,
    path: str,
) -> bool:
    expected = _expected_parent(identity)
    if expected is None:
        return parent_raw is None
    if parent_raw is None:
        return False
    try:
        actual = _parse_identity(parent_raw, path=path)
    except ComparisonArtifactError:
        return False
    return actual.to_dict() == expected.to_dict()


def _property_values_materially_different(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    base_has = bool(baseline.get("has_value"))
    cand_has = bool(candidate.get("has_value"))
    if base_has != cand_has:
        return True
    if not base_has:
        return False
    return canonical_value_fingerprint(baseline["value"]) != canonical_value_fingerprint(
        candidate["value"]
    )


def _validate_canonical_property_pointer(pointer: str, *, path: str) -> None:
    try:
        parsed = PropertyPath.parse(pointer)
    except (TypeError, ValueError) as exc:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="invalid property pointer",
                )
            ]
        ) from exc
    if parsed.to_pointer() != pointer:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path=path,
                    message="property pointer is not canonical",
                )
            ]
        )


def _validate_producer_metadata(payload: dict[str, Any]) -> None:
    baseline = payload.get("baseline")
    candidate = payload.get("candidate")
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/",
                    message="invalid comparison baseline or candidate metadata",
                )
            ]
        )

    if baseline.get("system_type") != candidate.get("system_type"):
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/candidate/system_type",
                    message="baseline and candidate system_type must match",
                )
            ]
        )

    if baseline.get("scanner") != candidate.get("scanner"):
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/candidate/scanner",
                    message="baseline and candidate scanner must match",
                )
            ]
        )

    for side in ("baseline", "candidate"):
        meta = baseline if side == "baseline" else candidate
        if meta.get("scanner_contract_version") != SCANNER_CONTRACT_VERSION:
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path=f"/{side}/scanner_contract_version",
                        message="unsupported scanner contract version",
                    )
                ]
            )


def _validate_root_alignment(payload: dict[str, Any]) -> None:
    baseline = payload["baseline"]
    candidate = payload["candidate"]
    alignment = payload.get("root_alignment")
    if not isinstance(alignment, dict):
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/root_alignment",
                    message="invalid root alignment block",
                )
            ]
        )

    source_names_equal = baseline["source_name"] == candidate["source_name"]
    database_names_equal = baseline["database_name"] == candidate["database_name"]
    source_alignment = alignment.get("source")
    database_alignment = alignment.get("database")

    if source_names_equal:
        if source_alignment is not None:
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path="/root_alignment/source",
                        message="root alignment source must be null when source names match",
                    )
                ]
            )
    elif source_alignment != {
        "baseline": baseline["source_name"],
        "candidate": candidate["source_name"],
    }:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/root_alignment/source",
                    message="root alignment source does not match metadata",
                )
            ]
        )

    if database_names_equal:
        if database_alignment is not None:
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path="/root_alignment/database",
                        message="root alignment database must be null when database names match",
                    )
                ]
            )
    elif database_alignment != {
        "baseline": baseline["database_name"],
        "candidate": candidate["database_name"],
    }:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/root_alignment/database",
                    message="root alignment database does not match metadata",
                )
            ]
        )


def _ancestors(identity: ComparisonObjectIdentity) -> list[ComparisonObjectIdentity]:
    kind = identity.kind
    path = identity.path
    ancestors: list[ComparisonObjectIdentity] = []
    if kind in ("column", "primary_key", "foreign_key", "relationship"):
        ancestors.append(ComparisonObjectIdentity("table", path[:2]))
    if kind in ("column", "primary_key", "foreign_key", "relationship", "table"):
        ancestors.append(ComparisonObjectIdentity("schema", (path[0],)))
    if kind == "database":
        ancestors.append(ComparisonObjectIdentity("data_source", ()))
    elif kind != "data_source":
        ancestors.append(ComparisonObjectIdentity("database", ()))
        ancestors.append(ComparisonObjectIdentity("data_source", ()))
    return ancestors


def _validate_hierarchy_consistency(
    parsed_changes: list[tuple[dict[str, Any], ComparisonObjectIdentity]],
) -> None:
    change_by_identity = {
        identity.canonical_bytes(): item["change"] for item, identity in parsed_changes
    }
    for item, identity in parsed_changes:
        child_change = item["change"]
        for ancestor in _ancestors(identity):
            ancestor_key = ancestor.canonical_bytes()
            if ancestor_key not in change_by_identity:
                continue
            ancestor_change = change_by_identity[ancestor_key]
            if ancestor_change not in {"added", "removed"}:
                continue
            if child_change != ancestor_change:
                raise ComparisonArtifactError(
                    [
                        ComparisonArtifactDiagnostic(
                            code="invalid_artifact",
                            path="/object_changes",
                            message="object change hierarchy is inconsistent",
                        )
                    ]
                )


def _validate_root_object_changes(
    parsed_changes: list[tuple[dict[str, Any], ComparisonObjectIdentity]],
) -> None:
    for item, identity in parsed_changes:
        if (
            identity.kind in ("data_source", "database")
            and identity.path == ()
            and item["change"] in {"added", "removed"}
        ):
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path="/object_changes",
                        message="root object changes must not be added or removed",
                    )
                ]
            )


def _find_name_property_change(
    parsed_changes: list[tuple[dict[str, Any], ComparisonObjectIdentity]],
    kind: str,
) -> dict[str, Any] | None:
    for item, identity in parsed_changes:
        if identity.kind != kind or identity.path != ():
            continue
        if item["change"] != "changed":
            continue
        for prop_change in item["property_changes"]:
            if prop_change["property"] == "/name":
                return prop_change
    return None


def _validate_root_name_metadata(
    payload: dict[str, Any],
    parsed_changes: list[tuple[dict[str, Any], ComparisonObjectIdentity]],
) -> None:
    baseline = payload["baseline"]
    candidate = payload["candidate"]

    for kind, baseline_key, candidate_key in (
        ("data_source", "source_name", "source_name"),
        ("database", "database_name", "database_name"),
    ):
        names_equal = baseline[baseline_key] == candidate[candidate_key]
        name_change = _find_name_property_change(parsed_changes, kind)
        if names_equal:
            if name_change is not None:
                raise ComparisonArtifactError(
                    [
                        ComparisonArtifactDiagnostic(
                            code="invalid_artifact",
                            path="/object_changes",
                            message=f"redundant {kind} name change when metadata names match",
                        )
                    ]
                )
            continue

        if name_change is None:
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path="/object_changes",
                        message=f"missing required {kind} name change",
                    )
                ]
            )

        baseline_side = name_change["baseline"]
        candidate_side = name_change["candidate"]
        expected_baseline = {"has_value": True, "value": baseline[baseline_key]}
        expected_candidate = {"has_value": True, "value": candidate[candidate_key]}
        if baseline_side != expected_baseline or candidate_side != expected_candidate:
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path="/object_changes",
                        message=f"{kind} name change does not match metadata",
                    )
                ]
            )


def validate_comparison_result_semantics(payload: dict[str, Any]) -> None:
    """Validate producer invariants and trust-boundary semantics on a comparison artifact."""
    if payload.get("direction") != DIRECTION:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/direction",
                    message="invalid comparison direction",
                )
            ]
        )
    if payload.get("writes_performed") != 0:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/writes_performed",
                    message="writes_performed must be zero",
                )
            ]
        )

    _validate_producer_metadata(payload)
    _validate_root_alignment(payload)

    summary = payload.get("summary")
    object_changes = payload.get("object_changes")
    status = payload.get("status")
    if not isinstance(summary, dict) or not isinstance(object_changes, list):
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/",
                    message="invalid comparison summary or object_changes",
                )
            ]
        )

    unchanged = summary.get("unchanged")
    if not isinstance(unchanged, int) or unchanged < 0:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/summary/unchanged",
                    message="summary unchanged must be a non-negative integer",
                )
            ]
        )

    seen_identities: set[bytes] = set()
    parsed_changes: list[tuple[dict[str, Any], ComparisonObjectIdentity]] = []

    for index, item in enumerate(object_changes):
        base_path = f"/object_changes/{index}"
        if not isinstance(item, dict):
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path=base_path,
                        message="invalid object change entry",
                    )
                ]
            )
        change = item.get("change")
        identity = _parse_identity(item["object_identity"], path=f"{base_path}/object_identity")
        identity_key = identity.canonical_bytes()
        if identity_key in seen_identities:
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path=f"{base_path}/object_identity",
                        message="duplicate object identity in object_changes",
                    )
                ]
            )
        seen_identities.add(identity_key)

        parent_raw = item.get("parent_identity")
        if not _parent_matches(identity, parent_raw, path=f"{base_path}/parent_identity"):
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path=f"{base_path}/parent_identity",
                        message="parent identity does not match object identity structure",
                    )
                ]
            )

        property_changes = item.get("property_changes")
        if change in {"added", "removed"}:
            if property_changes != []:
                raise ComparisonArtifactError(
                    [
                        ComparisonArtifactDiagnostic(
                            code="invalid_artifact",
                            path=f"{base_path}/property_changes",
                            message=(
                                "added or removed object change must have empty property_changes"
                            ),
                        )
                    ]
                )
        elif change == "changed":
            if not isinstance(property_changes, list) or len(property_changes) < 1:
                raise ComparisonArtifactError(
                    [
                        ComparisonArtifactDiagnostic(
                            code="invalid_artifact",
                            path=f"{base_path}/property_changes",
                            message="changed object change requires property_changes",
                        )
                    ]
                )
            seen_properties: set[str] = set()
            previous_pointer = ""
            for prop_index, prop_change in enumerate(property_changes):
                prop_path = f"{base_path}/property_changes/{prop_index}"
                if not isinstance(prop_change, dict):
                    raise ComparisonArtifactError(
                        [
                            ComparisonArtifactDiagnostic(
                                code="invalid_artifact",
                                path=prop_path,
                                message="invalid property change entry",
                            )
                        ]
                    )
                pointer = prop_change.get("property")
                if not isinstance(pointer, str):
                    raise ComparisonArtifactError(
                        [
                            ComparisonArtifactDiagnostic(
                                code="invalid_artifact",
                                path=f"{prop_path}/property",
                                message="property pointer is required",
                            )
                        ]
                    )
                _validate_canonical_property_pointer(pointer, path=f"{prop_path}/property")
                if pointer in seen_properties:
                    raise ComparisonArtifactError(
                        [
                            ComparisonArtifactDiagnostic(
                                code="invalid_artifact",
                                path=f"{prop_path}/property",
                                message="duplicate property pointer in property_changes",
                            )
                        ]
                    )
                seen_properties.add(pointer)
                if previous_pointer and pointer < previous_pointer:
                    raise ComparisonArtifactError(
                        [
                            ComparisonArtifactDiagnostic(
                                code="invalid_artifact",
                                path=f"{prop_path}/property",
                                message="property_changes are not canonically ordered",
                            )
                        ]
                    )
                previous_pointer = pointer

                if not comparison_change_property_compatible_with_kind(identity.kind, pointer):
                    raise ComparisonArtifactError(
                        [
                            ComparisonArtifactDiagnostic(
                                code="invalid_artifact",
                                path=f"{prop_path}/property",
                                message="property is not compatible with object kind",
                            )
                        ]
                    )

                baseline = prop_change.get("baseline")
                candidate = prop_change.get("candidate")
                if not isinstance(baseline, dict) or not isinstance(candidate, dict):
                    raise ComparisonArtifactError(
                        [
                            ComparisonArtifactDiagnostic(
                                code="invalid_artifact",
                                path=prop_path,
                                message="property change requires baseline and candidate",
                            )
                        ]
                    )
                if not comparable_change_value_compatible(identity, pointer, baseline):
                    raise ComparisonArtifactError(
                        [
                            ComparisonArtifactDiagnostic(
                                code="invalid_artifact",
                                path=f"{prop_path}/baseline",
                                message=(
                                    "property value is not compatible with "
                                    "comparison producer contract"
                                ),
                            )
                        ]
                    )
                if not comparable_change_value_compatible(identity, pointer, candidate):
                    raise ComparisonArtifactError(
                        [
                            ComparisonArtifactDiagnostic(
                                code="invalid_artifact",
                                path=f"{prop_path}/candidate",
                                message=(
                                    "property value is not compatible with "
                                    "comparison producer contract"
                                ),
                            )
                        ]
                    )
                if not _property_values_materially_different(baseline, candidate):
                    raise ComparisonArtifactError(
                        [
                            ComparisonArtifactDiagnostic(
                                code="invalid_artifact",
                                path=prop_path,
                                message="property change is not materially different",
                            )
                        ]
                    )
            if identity.kind == "foreign_key" and not foreign_key_reference_sides_coherent(
                property_changes
            ):
                raise ComparisonArtifactError(
                    [
                        ComparisonArtifactDiagnostic(
                            code="invalid_artifact",
                            path=f"{base_path}/property_changes",
                            message="foreign key reference properties are inconsistent",
                        )
                    ]
                )
        else:
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path=f"{base_path}/change",
                        message="invalid object change type",
                    )
                ]
            )

        parsed_changes.append((item, identity))

    _validate_root_object_changes(parsed_changes)
    _validate_root_name_metadata(payload, parsed_changes)
    _validate_hierarchy_consistency(parsed_changes)

    previous_rank = -1
    previous_identity_bytes = b""
    for item, identity in parsed_changes:
        rank = CHANGE_RANK[item["change"]]
        identity_bytes = identity.canonical_bytes()
        sort_key = (rank, identity_bytes)
        previous_key = (previous_rank, previous_identity_bytes)
        if previous_rank >= 0 and sort_key < previous_key:
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path="/object_changes",
                        message="object_changes are not canonically ordered",
                    )
                ]
            )
        previous_rank = rank
        previous_identity_bytes = identity_bytes

    added = sum(1 for item, _ in parsed_changes if item["change"] == "added")
    removed = sum(1 for item, _ in parsed_changes if item["change"] == "removed")
    changed = sum(1 for item, _ in parsed_changes if item["change"] == "changed")
    property_changes_count = sum(
        len(item["property_changes"]) for item, _ in parsed_changes if item["change"] == "changed"
    )
    material_count = added + removed + changed

    if summary.get("added") != added:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/summary/added",
                    message="summary added count mismatch",
                )
            ]
        )
    if summary.get("removed") != removed:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/summary/removed",
                    message="summary removed count mismatch",
                )
            ]
        )
    if summary.get("changed") != changed:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/summary/changed",
                    message="summary changed count mismatch",
                )
            ]
        )
    if summary.get("property_changes") != property_changes_count:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/summary/property_changes",
                    message="summary property_changes count mismatch",
                )
            ]
        )

    if status == "identical":
        if material_count != 0 or object_changes:
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path="/status",
                        message="identical status requires no material object changes",
                    )
                ]
            )
    elif status == "different":
        if material_count <= 0:
            raise ComparisonArtifactError(
                [
                    ComparisonArtifactDiagnostic(
                        code="invalid_artifact",
                        path="/status",
                        message="different status requires material object changes",
                    )
                ]
            )
    else:
        raise ComparisonArtifactError(
            [
                ComparisonArtifactDiagnostic(
                    code="invalid_artifact",
                    path="/status",
                    message="invalid comparison status",
                )
            ]
        )


__all__ = [
    "ComparisonArtifactDiagnostic",
    "ComparisonArtifactError",
    "ComparisonArtifactErrorCode",
    "load_comparison_artifact",
    "validate_comparison_result_semantics",
]
