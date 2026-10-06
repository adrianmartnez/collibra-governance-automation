"""Semantic validation for governance.yaml v2."""

from __future__ import annotations

import re
from typing import Any

from governance.config_contract.env_refs import assert_json_compatible
from governance.config_contract.errors import (
    CODE_SEMANTIC,
    ConfigSemanticError,
    DiagnosticError,
)
from governance.config_contract.paths import normalize_relative_path

_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def validate_semantics_v2(document: dict[str, Any]) -> None:
    errors: list[DiagnosticError] = []

    def add(path: str, message: str) -> None:
        errors.append(DiagnosticError(code=CODE_SEMANTIC, path=path, message=message))

    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        add("/sources", "sources must be a non-empty array")
    else:
        seen: set[str] = set()
        for index, entry in enumerate(sources):
            base = f"/sources/{index}"
            _validate_bound(entry, base=base, seen=seen, errors=errors)

    if "targets" in document:
        targets = document.get("targets")
        if not isinstance(targets, list) or not targets:
            add("/targets", "targets must be a non-empty array when present")
        else:
            seen_t: set[str] = set()
            for index, entry in enumerate(targets):
                base = f"/targets/{index}"
                _validate_bound(entry, base=base, seen=seen_t, errors=errors)

    artifacts = document.get("artifacts") or {}
    if artifacts is not None and not isinstance(artifacts, dict):
        add("/artifacts", "artifacts must be a mapping")
    elif isinstance(artifacts, dict):
        for key in ("inventory_path", "snapshot_path"):
            if key in artifacts:
                try:
                    normalize_relative_path(artifacts[key], pointer=f"/artifacts/{key}")
                except ConfigSemanticError as exc:
                    errors.extend(exc.errors)

    for section in ("policies", "authority"):
        block = document.get(section) or {}
        if block is None:
            continue
        if not isinstance(block, dict):
            add(f"/{section}", f"{section} must be a mapping")
            continue
        files = block.get("files", [])
        if files is None:
            continue
        if not isinstance(files, list):
            add(f"/{section}/files", "files must be an array")
            continue
        for index, item in enumerate(files):
            try:
                normalize_relative_path(item, pointer=f"/{section}/files/{index}")
            except ConfigSemanticError as exc:
                errors.extend(exc.errors)

    if errors:
        raise ConfigSemanticError(errors)


def _validate_bound(
    entry: object,
    *,
    base: str,
    seen: set[str],
    errors: list[DiagnosticError],
) -> None:
    if not isinstance(entry, dict):
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=base,
                message="provider entry must be a mapping",
            )
        )
        return
    logical_id = entry.get("id")
    if not isinstance(logical_id, str) or not logical_id.strip():
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=f"{base}/id",
                message="id must be a non-empty string",
            )
        )
    else:
        if logical_id in seen:
            errors.append(
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{base}/id",
                    message=f"duplicate logical id {logical_id!r}",
                )
            )
        seen.add(logical_id)

    provider = entry.get("provider")
    if not isinstance(provider, str) or _PROVIDER_ID_RE.fullmatch(provider) is None:
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=f"{base}/provider",
                message="provider id is invalid",
            )
        )

    config = entry.get("config")
    if not isinstance(config, dict):
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=f"{base}/config",
                message="config must be a mapping",
            )
        )
        return
    try:
        assert_json_compatible(config, path=f"{base}/config")
    except ConfigSemanticError as exc:
        errors.extend(exc.errors)
