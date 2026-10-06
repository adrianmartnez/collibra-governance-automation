"""Discover installed providers via Python packaging entry points."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from importlib import metadata

from governance.providers.contracts import (
    PROVIDER_ENTRY_POINT_GROUP,
    ProviderRegistration,
)
from governance.providers.errors import (
    CODE_BROKEN_ENTRY_POINT,
    CODE_DISCOVERY_FAILED,
    ProviderDiagnostic,
    ProviderDiscoveryError,
    ProviderError,
    sort_diagnostics,
)
from governance.providers.registry import ProviderRegistry


def discover_providers(
    *,
    entry_points: Sequence[metadata.EntryPoint] | Iterable[metadata.EntryPoint] | None = None,
) -> ProviderRegistry:
    """Load and validate providers from the ``governance.providers`` entry-point group.

    Discovery is metadata/registration only: no operational I/O, no secret
    resolution, and no capability factory invocation. Failures fail closed;
    a partial registry is never returned as success.
    """
    candidates = list(
        entry_points
        if entry_points is not None
        else metadata.entry_points().select(group=PROVIDER_ENTRY_POINT_GROUP)
    )
    ordered = sorted(candidates, key=_entry_point_sort_key)

    registry = ProviderRegistry()
    diagnostics: list[ProviderDiagnostic] = []

    for entry_point in ordered:
        ep_path = _entry_point_path(entry_point)
        try:
            loaded = entry_point.load()
        except Exception as exc:
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_BROKEN_ENTRY_POINT,
                    path=ep_path,
                    message=(
                        f"entry point {entry_point.name!r} failed to load ({type(exc).__name__})"
                    ),
                )
            )
            continue

        if not callable(loaded):
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_BROKEN_ENTRY_POINT,
                    path=ep_path,
                    message=f"entry point {entry_point.name!r} did not resolve to a callable",
                )
            )
            continue

        try:
            registration = loaded()
        except ProviderError as exc:
            diagnostics.extend(
                ProviderDiagnostic(
                    code=item.code,
                    path=f"{ep_path}{item.path if item.path.startswith('/') else '/' + item.path}",
                    message=item.message,
                )
                for item in exc.errors
            )
            continue
        except Exception as exc:
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_BROKEN_ENTRY_POINT,
                    path=ep_path,
                    message=(
                        f"entry point {entry_point.name!r} callable raised {type(exc).__name__}"
                    ),
                )
            )
            continue

        if not isinstance(registration, ProviderRegistration):
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_BROKEN_ENTRY_POINT,
                    path=ep_path,
                    message=(
                        f"entry point {entry_point.name!r} did not return ProviderRegistration"
                    ),
                )
            )
            continue

        try:
            registry.register(registration)
        except ProviderError as exc:
            diagnostics.extend(exc.errors)

    if diagnostics:
        raise ProviderDiscoveryError(
            [
                ProviderDiagnostic(
                    code=CODE_DISCOVERY_FAILED,
                    path="/discovery",
                    message="provider discovery failed; registry is invalid",
                ),
                *sort_diagnostics(diagnostics),
            ]
        )

    return registry


def _entry_point_sort_key(entry_point: metadata.EntryPoint) -> tuple[str, str, str, str]:
    dist_name, dist_version = _distribution_identity(entry_point)
    return (
        dist_name,
        dist_version,
        entry_point.name,
        entry_point.value,
    )


def _distribution_identity(entry_point: metadata.EntryPoint) -> tuple[str, str]:
    dist = getattr(entry_point, "dist", None)
    if dist is None:
        return ("", "")
    name = getattr(dist, "name", "") or ""
    version = ""
    try:
        version = str(dist.version)
    except Exception:
        version = ""
    return (str(name), version)


def _entry_point_path(entry_point: metadata.EntryPoint) -> str:
    dist_name, dist_version = _distribution_identity(entry_point)
    identity = dist_name or "unknown-distribution"
    if dist_version:
        identity = f"{identity}@{dist_version}"
    return f"/entry_points/{identity}/{entry_point.name}"
