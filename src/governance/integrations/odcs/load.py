"""Read and parse ODCS YAML/JSON documents."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from governance.integrations.odcs.errors import (
    CODE_PARSE,
    CODE_READ,
    OdcsDiagnostic,
    OdcsParseError,
    OdcsReadError,
)

_YAML_SUFFIXES = frozenset({".yaml", ".yml"})
_JSON_SUFFIXES = frozenset({".json"})


def load_odcs_document(path: str | Path) -> dict[str, Any]:
    """Load an ODCS document from a YAML or JSON file.

    Returns a deep-copied mapping. Does not validate apiVersion or schema.
    """
    target = Path(path)
    suffix = target.suffix.casefold()
    if suffix not in _YAML_SUFFIXES and suffix not in _JSON_SUFFIXES:
        raise OdcsReadError(
            [
                OdcsDiagnostic(
                    code=CODE_READ,
                    path="",
                    message="unsupported ODCS document file extension",
                )
            ]
        )

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise OdcsReadError(
            [
                OdcsDiagnostic(
                    code=CODE_READ,
                    path="",
                    message="unable to read ODCS document file",
                )
            ]
        ) from exc

    document = _parse_yaml(text) if suffix in _YAML_SUFFIXES else _parse_json(text)

    if not isinstance(document, dict):
        raise OdcsParseError(
            [
                OdcsDiagnostic(
                    code=CODE_PARSE,
                    path="",
                    message="ODCS document root must be a mapping",
                )
            ]
        )
    return copy.deepcopy(document)


def _parse_yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OdcsParseError(
            [
                OdcsDiagnostic(
                    code=CODE_PARSE,
                    path="",
                    message="invalid YAML in ODCS document",
                )
            ]
        ) from exc


def _reject_non_finite_json_constant(value: str) -> Any:
    raise OdcsParseError(
        [
            OdcsDiagnostic(
                code=CODE_PARSE,
                path="",
                message="invalid JSON in ODCS document",
            )
        ]
    )


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text, parse_constant=_reject_non_finite_json_constant)
    except OdcsParseError:
        raise
    except json.JSONDecodeError as exc:
        raise OdcsParseError(
            [
                OdcsDiagnostic(
                    code=CODE_PARSE,
                    path="",
                    message="invalid JSON in ODCS document",
                )
            ]
        ) from exc
