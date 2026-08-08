"""Safe YAML parsing for governance.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from governance.config_contract.errors import (
    CODE_PARSE,
    ConfigParseError,
    DiagnosticError,
)


def parse_governance_yaml(path: str | Path) -> Any:
    """Parse YAML with SafeLoader. Returns the raw document object."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigParseError(
            [
                DiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="unable to read governance configuration file",
                )
            ]
        ) from exc

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigParseError(
            [
                DiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="invalid YAML in governance configuration file",
                )
            ]
        ) from exc
