"""Orchestration helpers for provider-driven core workflows."""

from __future__ import annotations

__all__ = [
    "RuntimeConfiguration",
    "V1RuntimeConfiguration",
    "V2RuntimeConfiguration",
    "build_provider_registry",
    "load_runtime_configuration",
    "resolve_v2_providers",
]


def __getattr__(name: str):
    if name == "build_provider_registry":
        from governance.orchestration.registry import build_provider_registry

        return build_provider_registry
    if name in {
        "RuntimeConfiguration",
        "V1RuntimeConfiguration",
        "V2RuntimeConfiguration",
        "load_runtime_configuration",
        "resolve_v2_providers",
    }:
        from governance.orchestration import config as orchestration_config

        return getattr(orchestration_config, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
