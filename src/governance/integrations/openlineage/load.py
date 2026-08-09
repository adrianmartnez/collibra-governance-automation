"""Read and parse OpenLineage event JSON documents."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from governance.integrations.openlineage.errors import (
    CODE_PARSE,
    CODE_READ,
    OpenLineageDiagnostic,
    OpenLineageParseError,
    OpenLineageReadError,
)

_JSON_SUFFIXES = frozenset({".json"})


def load_openlineage_events(path: str | Path) -> tuple[dict[str, Any], ...]:
    """Load OpenLineage events from a JSON file.

    Accepts a single event object or a JSON array of events. Returns a
    deep-copied tuple of event mappings. Does not validate schemaURL or shape.
    """
    target = Path(path)
    suffix = target.suffix.casefold()
    if suffix not in _JSON_SUFFIXES:
        raise OpenLineageReadError(
            [
                OpenLineageDiagnostic(
                    code=CODE_READ,
                    path="",
                    message="unsupported OpenLineage file extension",
                )
            ]
        )

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpenLineageReadError(
            [
                OpenLineageDiagnostic(
                    code=CODE_READ,
                    path="",
                    message="unable to read OpenLineage file",
                )
            ]
        ) from exc

    document = _parse_json(text)
    return _normalize_root(document)


def _reject_non_finite_json_constant(value: str) -> Any:
    raise OpenLineageParseError(
        [
            OpenLineageDiagnostic(
                code=CODE_PARSE,
                path="",
                message="invalid JSON in OpenLineage document",
            )
        ]
    )


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text, parse_constant=_reject_non_finite_json_constant)
    except OpenLineageParseError:
        raise
    except json.JSONDecodeError as exc:
        raise OpenLineageParseError(
            [
                OpenLineageDiagnostic(
                    code=CODE_PARSE,
                    path="",
                    message="invalid JSON in OpenLineage document",
                )
            ]
        ) from exc


def _normalize_root(document: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(document, dict):
        return (copy.deepcopy(document),)

    if isinstance(document, list):
        events: list[dict[str, Any]] = []
        for index, item in enumerate(document):
            if not isinstance(item, dict):
                raise OpenLineageParseError(
                    [
                        OpenLineageDiagnostic(
                            code=CODE_PARSE,
                            path=f"/{index}",
                            message="OpenLineage event must be a mapping",
                        )
                    ]
                )
            events.append(copy.deepcopy(item))
        return tuple(events)

    raise OpenLineageParseError(
        [
            OpenLineageDiagnostic(
                code=CODE_PARSE,
                path="",
                message="OpenLineage root must be a mapping or array",
            )
        ]
    )
