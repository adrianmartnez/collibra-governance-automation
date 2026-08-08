"""Read and parse dbt Manifest JSON documents."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from governance.integrations.dbt.errors import (
    CODE_PARSE,
    CODE_READ,
    DbtDiagnostic,
    DbtParseError,
    DbtReadError,
)

_JSON_SUFFIXES = frozenset({".json"})


def load_dbt_manifest(path: str | Path) -> dict[str, Any]:
    """Load a dbt Manifest from a JSON file.

    Returns a deep-copied mapping. Does not validate schema version or shape.
    """
    target = Path(path)
    suffix = target.suffix.casefold()
    if suffix not in _JSON_SUFFIXES:
        raise DbtReadError(
            [
                DbtDiagnostic(
                    code=CODE_READ,
                    path="",
                    message="unsupported dbt Manifest file extension",
                )
            ]
        )

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise DbtReadError(
            [
                DbtDiagnostic(
                    code=CODE_READ,
                    path="",
                    message="unable to read dbt Manifest file",
                )
            ]
        ) from exc

    document = _parse_json(text)

    if not isinstance(document, dict):
        raise DbtParseError(
            [
                DbtDiagnostic(
                    code=CODE_PARSE,
                    path="",
                    message="dbt Manifest root must be a mapping",
                )
            ]
        )
    return copy.deepcopy(document)


def _reject_non_finite_json_constant(value: str) -> Any:
    raise DbtParseError(
        [
            DbtDiagnostic(
                code=CODE_PARSE,
                path="",
                message="invalid JSON in dbt Manifest",
            )
        ]
    )


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text, parse_constant=_reject_non_finite_json_constant)
    except DbtParseError:
        raise
    except json.JSONDecodeError as exc:
        raise DbtParseError(
            [
                DbtDiagnostic(
                    code=CODE_PARSE,
                    path="",
                    message="invalid JSON in dbt Manifest",
                )
            ]
        ) from exc
