"""Unified v1/v2 operation runtime loading and binding selection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from governance.authority.load import load_normalized_authority_from_files_config
from governance.config import Settings
from governance.config_contract.errors import (
    CODE_SEMANTIC,
    ConfigContractError,
    ConfigSemanticError,
    DiagnosticError,
)
from governance.config_contract.models import CanonicalConfig
from governance.config_contract.models_v2 import CanonicalConfigV2, LoadedConfigV2
from governance.config_contract.provider_resolution import (
    ResolvedProviderBinding,
    ResolvedProviderConfiguration,
)
from governance.config_contract.resolution_diagnostics import CODE_RUNTIME_INVALID
from governance.config_contract.resolve import ConfigResolutionError, resolve_settings
from governance.domain.authority import NormalizedAuthorityPolicySet
from governance.domain.models import GovernanceModel
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.orchestration.compat_v1 import (
    collibra_binding_from_settings,
    postgresql_binding_from_settings,
)
from governance.orchestration.config import (
    V1RuntimeConfiguration,
    load_runtime_configuration,
    resolve_v2_providers,
)
from governance.orchestration.registry import build_provider_registry
from governance.orchestration.selection import (
    require_unique_binding,
    select_bindings_with_capability,
)
from governance.orchestration.sources import run_metadata_discovery
from governance.providers import CapabilityId
from governance.providers.builtins.collibra import collibra_runtime_from_context
from governance.providers.errors import ProviderError
from governance.providers.registry import ProviderRegistry


@dataclass(frozen=True, slots=True)
class OperationRuntime:
    registry: ProviderRegistry
    authority: NormalizedAuthorityPolicySet
    kind: Literal["1", "2"]
    canonical_v1: CanonicalConfig | None = None
    settings: Settings | None = None
    loaded_v2: LoadedConfigV2 | None = None
    resolved: ResolvedProviderConfiguration | None = None

    @property
    def policies_canonical(self) -> CanonicalConfig | CanonicalConfigV2:
        if self.kind == "1":
            assert self.canonical_v1 is not None
            return self.canonical_v1
        assert self.loaded_v2 is not None
        return self.loaded_v2.canonical

    @property
    def config_root(self) -> str:
        return self.policies_canonical.config_root

    @property
    def has_targets(self) -> bool:
        canonical = self.policies_canonical
        if isinstance(canonical, CanonicalConfig):
            return bool(canonical.targets)
        return bool(canonical.targets)


def load_operation_runtime(
    config_path: str,
    profile: str | None,
) -> OperationRuntime:
    """Load v1 or v2 configuration with registry, authority, and resolved bindings."""
    runtime_cfg = load_runtime_configuration(config_path, profile=profile)
    registry = build_provider_registry(discover_external=True)
    if isinstance(runtime_cfg, V1RuntimeConfiguration):
        canonical = runtime_cfg.canonical
        authority = load_normalized_authority_from_files_config(
            authority=canonical.authority,
            config_root=canonical.config_root,
        )
        return OperationRuntime(
            registry=registry,
            authority=authority,
            kind="1",
            canonical_v1=canonical,
            settings=None,
        )
    loaded = runtime_cfg.loaded
    resolved = resolve_v2_providers(runtime_cfg, registry=registry)
    authority = load_normalized_authority_from_files_config(
        authority=loaded.canonical.authority,
        config_root=loaded.canonical.config_root,
    )
    return OperationRuntime(
        registry=registry,
        authority=authority,
        kind="2",
        loaded_v2=loaded,
        resolved=resolved,
    )


def _v1_settings(runtime: OperationRuntime) -> Settings:
    assert runtime.kind == "1"
    assert runtime.canonical_v1 is not None
    if runtime.settings is not None:
        return runtime.settings
    return resolve_settings(runtime.canonical_v1)


def metadata_binding(runtime: OperationRuntime) -> ResolvedProviderBinding:
    if runtime.kind == "1":
        return postgresql_binding_from_settings(_v1_settings(runtime), runtime.registry)
    assert runtime.resolved is not None
    bindings = select_bindings_with_capability(
        runtime.resolved,
        role="source",
        capability=CapabilityId.METADATA_DISCOVERY,
    )
    return require_unique_binding(
        bindings,
        capability=CapabilityId.METADATA_DISCOVERY,
        role="source",
    )


def collibra_binding(
    runtime: OperationRuntime,
    mapping_config: CollibraMappingConfig | None = None,
) -> ResolvedProviderBinding:
    """Collibra target for plan/apply/diff/sync (``remote_state_read`` / ``target_planning``).

    Preflight uses :func:`preflight_target_binding` with ``compatibility_preflight`` selection.
    """
    if runtime.kind == "1":
        assert mapping_config is not None
        return collibra_binding_from_settings(
            _v1_settings(runtime),
            mapping_config,
            runtime.registry,
        )
    assert runtime.resolved is not None
    capability = CapabilityId.REMOTE_STATE_READ
    remote_bindings = select_bindings_with_capability(
        runtime.resolved,
        role="target",
        capability=capability,
    )
    if not remote_bindings:
        capability = CapabilityId.TARGET_PLANNING
        remote_bindings = select_bindings_with_capability(
            runtime.resolved,
            role="target",
            capability=capability,
        )
    binding = require_unique_binding(
        remote_bindings,
        capability=capability,
        role="target",
    )
    if binding.provider_id != "collibra":
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=binding.config_path or "/targets",
                    message=(
                        "desired-state plan/apply/diff/sync requires a collibra target; "
                        f"found provider {binding.provider_id!r}"
                    ),
                )
            ]
        )
    return binding


def _provider_config_resolution_error(
    exc: ProviderError,
    *,
    config_path: str,
) -> ConfigResolutionError:
    diagnostic = exc.errors[0]
    rel = diagnostic.path
    if not rel or rel == "/":
        path = f"{config_path}/config"
    elif rel.startswith("/"):
        path = f"{config_path}/config{rel}"
    else:
        path = f"{config_path}/config/{rel}"
    return ConfigResolutionError(
        diagnostic.message,
        path=path,
        code=CODE_RUNTIME_INVALID,
    )


def _postgres_params_from_metadata_binding(
    metadata: ResolvedProviderBinding,
) -> object:
    from governance.providers.builtins.postgresql import _connection_params_from_config

    if metadata.provider_id != "postgresql":
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=metadata.config_path or "/sources",
                    message=(
                        "Collibra Settings bridging requires a postgresql metadata source "
                        f"(metadata_discovery); found provider {metadata.provider_id!r}. "
                        "Use scan/check/export via provider capabilities without Settings, "
                        "or add a postgresql source for legacy Collibra workflows."
                    ),
                )
            ]
        )
    try:
        return _connection_params_from_config(metadata.runtime_context.config)
    except ProviderError as exc:
        raise _provider_config_resolution_error(exc, config_path=metadata.config_path) from exc


def settings_for_operation(runtime: OperationRuntime) -> Settings:
    """Collibra/v1 compatibility ``Settings`` projection — NOT a provider-neutral runtime.

    v2 requires the unique ``metadata_discovery`` binding to be ``provider_id=='postgresql'``.
    Scan, check, and export must use :func:`scan_model_from_runtime` instead.
    """
    if runtime.kind == "1":
        return _v1_settings(runtime)
    metadata = metadata_binding(runtime)
    params = _postgres_params_from_metadata_binding(metadata)
    collibra = collibra_binding(runtime)
    try:
        collibra_runtime = collibra_runtime_from_context(collibra.runtime_context)
    except ProviderError as exc:
        raise _provider_config_resolution_error(exc, config_path=collibra.config_path) from exc
    merged = collibra_runtime.build_settings()
    return replace(
        merged,
        postgres_host=params.host,
        postgres_port=params.port,
        postgres_db=params.db,
        postgres_user=params.user,
        postgres_password=params.password,
        postgres_source_name=params.source_name,
    )


def mapping_for_operation(runtime: OperationRuntime) -> CollibraMappingConfig:
    if runtime.kind == "1":
        raise ValueError("v1 mapping requires explicit resolution from canonical config")
    collibra = collibra_binding(runtime)
    return collibra_runtime_from_context(collibra.runtime_context).mapping_config


def scan_model_from_runtime(runtime: OperationRuntime) -> GovernanceModel:
    return run_metadata_discovery(metadata_binding(runtime))


def preflight_target_binding(runtime: OperationRuntime) -> ResolvedProviderBinding:
    if runtime.kind == "1":
        settings = _v1_settings(runtime)
        mapping = _v1_collibra_mapping_for_preflight(runtime, settings)
        return collibra_binding_from_settings(settings, mapping, runtime.registry)
    assert runtime.resolved is not None
    bindings = select_bindings_with_capability(
        runtime.resolved,
        role="target",
        capability=CapabilityId.COMPATIBILITY_PREFLIGHT,
    )
    binding = require_unique_binding(
        bindings,
        capability=CapabilityId.COMPATIBILITY_PREFLIGHT,
        role="target",
    )
    if binding.provider_id != "collibra":
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=binding.config_path or "/targets",
                    message=(
                        "compatibility preflight requires a collibra target; "
                        f"found provider {binding.provider_id!r}"
                    ),
                )
            ]
        )
    return binding


def _v1_collibra_mapping_for_preflight(
    runtime: OperationRuntime,
    settings: Settings,
) -> CollibraMappingConfig:
    from governance.config_contract.resolve import resolve_mapping_path
    from governance.integrations.collibra.mapping import (
        CollibraMappingError,
        load_mapping_config_file,
        mock_mapping_config,
    )

    assert runtime.canonical_v1 is not None
    mode = settings.collibra_mode.strip().lower()
    if mode == "mock":
        return mock_mapping_config()
    mapped = resolve_mapping_path(runtime.canonical_v1)
    if mapped is None:
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path="/targets",
                    message="collibra mapping is required for preflight when mode is not mock",
                )
            ]
        )
    try:
        return load_mapping_config_file(str(mapped))
    except CollibraMappingError as exc:
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=str(mapped),
                    message=str(exc) or "invalid collibra mapping",
                )
            ]
        ) from exc


def target_context_projection_for_runtime(runtime: OperationRuntime) -> dict[str, object]:
    """Build target context projection; v2 uses metadata ``source_name`` for sync_v2."""
    if runtime.kind == "1":
        from governance.plans.target_context import build_target_context_projection

        return build_target_context_projection(_v1_settings(runtime))
    metadata = metadata_binding(runtime)
    params = _postgres_params_from_metadata_binding(metadata)
    collibra = collibra_binding(runtime)
    collibra_runtime = collibra_runtime_from_context(collibra.runtime_context)
    return collibra_runtime.build_target_context(source_name=params.source_name)


def map_provider_errors(exc: BaseException) -> ConfigContractError:
    from governance.providers.discovery import ProviderDiscoveryError
    from governance.providers.errors import ProviderRegistryError

    if isinstance(exc, ConfigSemanticError):
        return ConfigContractError(list(exc.errors))
    if isinstance(exc, (ProviderRegistryError, ProviderDiscoveryError)):
        return ConfigContractError(
            [
                DiagnosticError(code=item.code, path=item.path, message=item.message)
                for item in exc.errors
            ]
        )
    raise exc


__all__ = [
    "OperationRuntime",
    "collibra_binding",
    "load_operation_runtime",
    "map_provider_errors",
    "mapping_for_operation",
    "metadata_binding",
    "preflight_target_binding",
    "scan_model_from_runtime",
    "settings_for_operation",
    "target_context_projection_for_runtime",
]
