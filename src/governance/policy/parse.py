"""Safe YAML parsing for policy files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from governance.policy.errors import CODE_PARSE, PolicyDiagnosticError, PolicyParseError


def parse_policy_yaml(path: str | Path, *, source: str) -> Any:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyParseError(
            [
                PolicyDiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="unable to read policy file",
                    source=source,
                )
            ]
        ) from exc

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyParseError(
            [
                PolicyDiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="invalid YAML in policy file",
                    source=source,
                )
            ]
        ) from exc
