"""Path normalization and safety for governance.yaml references."""

from __future__ import annotations

from pathlib import PurePosixPath

from governance.config_contract.errors import (
    CODE_SEMANTIC,
    ConfigSemanticError,
    DiagnosticError,
)


def normalize_relative_path(raw: str, *, pointer: str) -> str:
    """Normalize to POSIX relative path under config root; reject escapes."""
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=pointer,
                    message="path must be a non-empty relative string",
                )
            ]
        )
    text = raw.strip().replace("\\", "/")
    if text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=pointer,
                    message="path must be relative to the configuration file directory",
                )
            ]
        )

    parts: list[str] = []
    for part in PurePosixPath(text).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ConfigSemanticError(
                [
                    DiagnosticError(
                        code=CODE_SEMANTIC,
                        path=pointer,
                        message="path must not escape the configuration file directory",
                    )
                ]
            )
        parts.append(part)
    if not parts:
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=pointer,
                    message="path must be a non-empty relative string",
                )
            ]
        )
    return "/".join(parts)
