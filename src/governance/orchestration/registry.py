"""Compose built-in and discovered providers into a single registry."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from importlib import metadata

from governance.providers.builtins import builtin_provider_registrations
from governance.providers.discovery import discover_provider_registrations
from governance.providers.errors import ProviderRegistryError, sort_diagnostics
from governance.providers.registry import ProviderRegistry


def build_provider_registry(
    *,
    discover_external: bool = True,
    entry_points: Sequence[metadata.EntryPoint] | Iterable[metadata.EntryPoint] | None = None,
) -> ProviderRegistry:
    """Register built-in providers and optionally entry-point providers.

    Failures accumulate and raise ``ProviderRegistryError``; a partial registry is
    never returned as success.
    """
    registrations = list(builtin_provider_registrations())
    if discover_external:
        registrations.extend(discover_provider_registrations(entry_points=entry_points))

    registry = ProviderRegistry()
    diagnostics = []
    for registration in registrations:
        try:
            registry.register(registration)
        except ProviderRegistryError as exc:
            diagnostics.extend(exc.errors)

    if diagnostics:
        raise ProviderRegistryError(sort_diagnostics(diagnostics))

    return registry
