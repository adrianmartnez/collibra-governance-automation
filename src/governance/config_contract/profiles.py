"""Deterministic profile overlay for governance.yaml."""

from __future__ import annotations

from typing import Any

from governance.config_contract.errors import (
    CODE_SEMANTIC,
    ConfigSemanticError,
    DiagnosticError,
)

_OVERLAY_KEYS = ("sources", "targets", "artifacts", "policies", "authority")


def select_profile_name(
    *,
    cli_profile: str | None,
    env_profile: str | None,
) -> str | None:
    if cli_profile is not None and cli_profile.strip():
        return cli_profile.strip()
    if env_profile is not None and env_profile.strip():
        return env_profile.strip()
    return None


def apply_profile_overlay(document: dict[str, Any], profile_name: str | None) -> dict[str, Any]:
    """Apply selected profile overlay before final semantic validation."""
    if profile_name is None:
        return dict(document)

    profiles = document.get("profiles")
    if not isinstance(profiles, dict) or profile_name not in profiles:
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path="/profiles",
                    message=f"unknown profile {profile_name!r}",
                )
            ]
        )

    overlay = profiles[profile_name]
    if overlay is None:
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"/profiles/{profile_name}",
                    message="profile overlay must not be null",
                )
            ]
        )
    if not isinstance(overlay, dict):
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"/profiles/{profile_name}",
                    message="profile overlay must be a mapping",
                )
            ]
        )

    merged = {key: value for key, value in document.items() if key != "profiles"}
    for key in _OVERLAY_KEYS:
        if key not in overlay:
            continue
        value = overlay[key]
        if value is None:
            raise ConfigSemanticError(
                [
                    DiagnosticError(
                        code=CODE_SEMANTIC,
                        path=f"/profiles/{profile_name}/{key}",
                        message="null overlay values are not supported",
                    )
                ]
            )
        if key in {"artifacts", "policies", "authority"} and isinstance(value, dict):
            base = merged.get(key)
            if isinstance(base, dict):
                merged[key] = _deep_merge_maps(base, value, path=f"/profiles/{profile_name}/{key}")
            else:
                merged[key] = value
        else:
            # lists and other scalars/arrays: replace entirely
            merged[key] = value
    return merged


def _deep_merge_maps(base: dict[str, Any], overlay: dict[str, Any], *, path: str) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        child_path = f"{path}/{key}"
        if value is None:
            raise ConfigSemanticError(
                [
                    DiagnosticError(
                        code=CODE_SEMANTIC,
                        path=child_path,
                        message="null overlay values are not supported",
                    )
                ]
            )
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_maps(result[key], value, path=child_path)
        else:
            result[key] = value
    return result
