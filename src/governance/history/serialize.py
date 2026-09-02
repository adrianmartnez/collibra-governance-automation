"""Canonical JSON serialization and atomic writes for history artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from governance.history.errors import CODE_WRITE_ERROR, DiagnosticError, HistoryError
from governance.history.models import GovernanceHistory
from governance.io.atomic import atomic_write_text


def canonical_history_json(history: GovernanceHistory | dict[str, Any]) -> str:
    payload = history.to_dict() if isinstance(history, GovernanceHistory) else history
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def canonical_evolution_json(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_history_artifact(history: GovernanceHistory, path: str | Path) -> Path:
    target = Path(path)
    try:
        return atomic_write_text(target, canonical_history_json(history))
    except OSError as exc:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_WRITE_ERROR,
                    path="/",
                    message="unable to write history artifact",
                )
            ]
        ) from exc


def write_evolution_artifact(result: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    try:
        return atomic_write_text(target, canonical_evolution_json(result))
    except OSError as exc:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_WRITE_ERROR,
                    path="/output",
                    message="unable to write evolution artifact",
                )
            ]
        ) from exc


__all__ = [
    "canonical_evolution_json",
    "canonical_history_json",
    "write_evolution_artifact",
    "write_history_artifact",
]
