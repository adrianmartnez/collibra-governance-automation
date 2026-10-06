"""Resolve provider-bound v2 config before operational I/O and construct capabilities."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from governance.config_contract.env_refs import resolve_env_refs
from governance.config_contract.errors import (
    CODE_SEMANTIC,
    ConfigSemanticError,
    DiagnosticError,
)
from governance.config_contract.models_v2 import (
    CanonicalConfigV2,
    LoadedConfigV2,
    ProviderBoundConfig,
)
from governance.config_contract.resolve import ConfigResolutionError
from governance.providers import (
    CapabilityId,
    ProviderError,
    ProviderRegistration,
    ProviderRegistry,
    ProviderRuntimeContext,
)
from governance.providers.errors import (
    CODE_INVALID_CAPABILITY_BINDING,
    CODE_UNKNOWN_PROVIDER,
    ProviderDiagnostic,
    ProviderRegistryError,
    sort_diagnostics,
)


@dataclass(frozen=True, slots=True)
class ResolvedProviderBinding:
    role: Literal["source", "target"]
    logical_id: str
    provider_id: str
    registration: ProviderRegistration
    runtime_context: ProviderRuntimeContext
    config_path: str


@dataclass(frozen=True, slots=True)
class ResolvedProviderConfiguration:
    canonical: CanonicalConfigV2
    sources: tuple[ResolvedProviderBinding, ...]
    targets: tuple[ResolvedProviderBinding, ...]


class ProviderConstructionError(Exception):
    """Capability factory failed during pre-I/O runtime construction."""

    def __init__(self, errors: list[ProviderDiagnostic] | tuple[ProviderDiagnostic, ...]) -> None:
        self.errors = sort_diagnostics(errors)
        super().__init__(self.errors[0].message if self.errors else "provider construction failed")


def resolve_provider_configuration(
    loaded: LoadedConfigV2,
    *,
    registry: ProviderRegistry,
    environ: Mapping[str, str] | None = None,
) -> ResolvedProviderConfiguration:
    """Registry resolve → validator(unresolved) → env resolve → RuntimeContext.

    MUST NOT invoke capability factories. SDK compatibility is enforced at
    ``ProviderRegistry.register`` / discovery, not re-checked here.
    """
    if not isinstance(loaded, LoadedConfigV2):
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path="",
                    message="resolve_provider_configuration requires LoadedConfigV2",
                )
            ]
        )

    env = dict(os.environ if environ is None else environ)
    canonical = loaded.canonical
    sources = tuple(
        _resolve_one(
            entry,
            role="source",
            registry=registry,
            environ=env,
            config_path=loaded.locations.source_locations[entry.id],
            config_root=canonical.config_root,
        )
        for entry in canonical.sources
    )
    targets = tuple(
        _resolve_one(
            entry,
            role="target",
            registry=registry,
            environ=env,
            config_path=loaded.locations.target_locations[entry.id],
            config_root=canonical.config_root,
        )
        for entry in canonical.targets
    )
    return ResolvedProviderConfiguration(
        canonical=canonical,
        sources=sources,
        targets=targets,
    )


def construct_provider_capability(
    resolved: ResolvedProviderBinding,
    capability_id: CapabilityId | str,
) -> object:
    """Explicit post-resolution runtime construction boundary for #94."""
    if not isinstance(resolved, ResolvedProviderBinding):
        raise ProviderConstructionError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_CAPABILITY_BINDING,
                    path="/",
                    message="construct_provider_capability requires ResolvedProviderBinding",
                )
            ]
        )
    try:
        capability = (
            capability_id
            if isinstance(capability_id, CapabilityId)
            else CapabilityId(str(capability_id))
        )
    except ValueError as exc:
        raise ProviderConstructionError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_CAPABILITY_BINDING,
                    path=f"{resolved.config_path}/capabilities",
                    message=f"unknown capability id {capability_id!r}",
                )
            ]
        ) from exc

    try:
        binding = resolved.registration.binding_for(capability)
    except ProviderError as exc:
        raise ProviderConstructionError(
            [
                ProviderDiagnostic(
                    code=item.code,
                    path=f"{resolved.config_path}{item.path}",
                    message=item.message,
                )
                for item in exc.errors
            ]
        ) from exc

    try:
        return binding.factory(resolved.runtime_context)
    except ProviderError as exc:
        raise ProviderConstructionError(
            [
                ProviderDiagnostic(
                    code=item.code,
                    path=f"{resolved.config_path}{item.path}",
                    message=item.message,
                )
                for item in exc.errors
            ]
        ) from exc
    except Exception as exc:
        # Break the exception chain so original messages (which may include
        # resolved secrets from provider config) never surface in tracebacks.
        raise ProviderConstructionError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_CAPABILITY_BINDING,
                    path=resolved.config_path,
                    message=(
                        f"provider {resolved.provider_id!r} capability "
                        f"{capability.value!r} factory raised {type(exc).__name__}"
                    ),
                )
            ]
        ) from None


def _resolve_one(
    entry: ProviderBoundConfig,
    *,
    role: Literal["source", "target"],
    registry: ProviderRegistry,
    environ: Mapping[str, str],
    config_path: str,
    config_root: str,
) -> ResolvedProviderBinding:
    try:
        registration = registry.get(entry.provider_id)
    except ProviderRegistryError as exc:
        raise ProviderRegistryError(
            [
                ProviderDiagnostic(
                    code=item.code,
                    path=_registry_lookup_path(config_path, item),
                    message=item.message,
                )
                for item in exc.errors
            ]
        ) from exc

    unresolved = dict(entry.config)
    validator = registration.config_validator
    if validator is not None:
        try:
            validator.validate(unresolved)
        except ProviderError as exc:
            raise ProviderRegistryError(
                [
                    ProviderDiagnostic(
                        code=item.code,
                        path=_prefix_config_path(config_path, item.path),
                        message=item.message,
                    )
                    for item in exc.errors
                ]
            ) from exc
        except Exception as exc:
            raise ProviderRegistryError(
                [
                    ProviderDiagnostic(
                        code=CODE_INVALID_CAPABILITY_BINDING,
                        path=f"{config_path}/config",
                        message=(
                            f"provider {entry.provider_id!r} config validator raised "
                            f"{type(exc).__name__}"
                        ),
                    )
                ]
            ) from None

    try:
        resolved_config = resolve_env_refs(
            unresolved,
            environ,
            path=f"{config_path}/config",
        )
    except ConfigResolutionError:
        raise

    assert isinstance(resolved_config, Mapping)
    runtime_context = ProviderRuntimeContext(
        config=dict(resolved_config),
        config_root=config_root,
    )
    return ResolvedProviderBinding(
        role=role,
        logical_id=entry.id,
        provider_id=entry.provider_id,
        registration=registration,
        runtime_context=runtime_context,
        config_path=config_path,
    )


def _registry_lookup_path(config_path: str, item: ProviderDiagnostic) -> str:
    if item.code == CODE_UNKNOWN_PROVIDER:
        return f"{config_path}/provider"
    if item.path.startswith("/"):
        return f"{config_path}{item.path}"
    return f"{config_path}/{item.path}" if item.path else config_path


def _prefix_config_path(config_path: str, item_path: str) -> str:
    if not item_path or item_path == "/":
        return f"{config_path}/config"
    if item_path.startswith("/"):
        return f"{config_path}/config{item_path}"
    return f"{config_path}/config/{item_path}"
