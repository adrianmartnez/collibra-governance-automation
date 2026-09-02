"""Strict load path for governance-snapshot v1 artifacts."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

from governance.identity import ContentIdentity, snapshot_identity
from governance.identity.json_values import validate_json_value
from governance.snapshots.errors import (
    SnapshotCompatibilityError,
    SnapshotIntegrityError,
    SnapshotIOError,
)
from governance.snapshots.models import SNAPSHOT_SCHEMA, SNAPSHOT_VERSION, GovernanceSnapshot
from governance.snapshots.validate import validate_and_parse_snapshot_payload

_SCHEMA_RESOURCE = "governance-snapshot.v1.schema.json"
_validator: Draft202012Validator | None = None


def _load_schema() -> dict[str, Any]:
    text = (
        files("governance.snapshots.schemas").joinpath(_SCHEMA_RESOURCE).read_text(encoding="utf-8")
    )
    return json.loads(text)


def _get_validator() -> Draft202012Validator:
    global _validator
    if _validator is None:
        _validator = Draft202012Validator(_load_schema())
    return _validator


def _schema_error_path(error: jsonschema.ValidationError) -> str:
    path_parts = [str(part) for part in error.absolute_path]
    return "/" + "/".join(path_parts) if path_parts else "/"


def load_snapshot_artifact(path: str | Path) -> GovernanceSnapshot:
    """Load, schema-validate, integrity-check, and semantically parse a snapshot."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise SnapshotCompatibilityError(
            "invalid snapshot JSON",
            code="parse_error",
            path="/",
        ) from exc
    except OSError as exc:
        raise SnapshotIOError(
            f"Unable to read snapshot from {target}",
            code="read_error",
            path="/",
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
        raise SnapshotCompatibilityError(
            "invalid snapshot JSON",
            code="parse_error",
            path="/",
        ) from exc

    try:
        validate_json_value(payload)
    except (TypeError, ValueError, RecursionError) as exc:
        raise SnapshotCompatibilityError(
            "invalid snapshot JSON",
            code="parse_error",
            path="/",
        ) from exc

    if not isinstance(payload, dict):
        raise SnapshotCompatibilityError(
            "snapshot root must be a mapping",
            code="invalid_snapshot_root",
            path="/",
        )

    schema = payload.get("snapshot_schema")
    version = payload.get("snapshot_version")
    if schema != SNAPSHOT_SCHEMA:
        raise SnapshotCompatibilityError(
            "unsupported snapshot schema",
            code="unsupported_snapshot_schema",
            path="/snapshot_schema",
        )
    if version != SNAPSHOT_VERSION:
        raise SnapshotCompatibilityError(
            "unsupported snapshot version",
            code="unsupported_snapshot_version",
            path="/snapshot_version",
        )

    identity_raw = payload.get("content_identity")
    if not isinstance(identity_raw, dict):
        raise SnapshotCompatibilityError(
            "snapshot content_identity is required",
            code="missing_content_identity",
            path="/content_identity",
        )

    try:
        schema_errors = sorted(
            _get_validator().iter_errors(payload),
            key=lambda item: (
                "/" + "/".join(str(part) for part in item.absolute_path),
                item.validator,
            ),
        )
    except RecursionError as exc:
        raise SnapshotCompatibilityError(
            "snapshot payload is too deeply nested",
            code="invalid_snapshot_payload",
            path="/",
        ) from exc
    if schema_errors:
        error = schema_errors[0]
        raise SnapshotCompatibilityError(
            f"invalid snapshot payload ({error.validator})",
            code="invalid_snapshot_payload",
            path=_schema_error_path(error),
        )

    without_identity = {key: value for key, value in payload.items() if key != "content_identity"}
    expected = snapshot_identity(without_identity)

    algorithm = identity_raw.get("algorithm")
    hashing_contract_version = identity_raw.get("hashing_contract_version")
    digest = identity_raw.get("digest")
    if (
        not isinstance(algorithm, str)
        or not isinstance(hashing_contract_version, str)
        or not isinstance(digest, str)
    ):
        raise SnapshotIntegrityError(
            "snapshot content_identity mismatch",
            code="integrity_mismatch",
            path="/content_identity",
        )
    actual = ContentIdentity(
        algorithm=algorithm,
        hashing_contract_version=hashing_contract_version,
        digest=digest,
    )
    if actual != expected:
        raise SnapshotIntegrityError(
            "snapshot content_identity mismatch",
            code="integrity_mismatch",
            path="/content_identity",
        )

    return validate_and_parse_snapshot_payload(payload)


__all__ = ["load_snapshot_artifact"]
