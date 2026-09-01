"""Safe YAML parsing for authority files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from governance.authority.errors import (
    CODE_PARSE,
    AuthorityDiagnosticError,
    AuthorityParseError,
)


def parse_authority_yaml(path: str | Path, *, source: str) -> Any:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuthorityParseError(
            [
                AuthorityDiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="unable to read authority file",
                    source=source,
                )
            ]
        ) from exc

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AuthorityParseError(
            [
                AuthorityDiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="invalid YAML in authority file",
                    source=source,
                )
            ]
        ) from exc
