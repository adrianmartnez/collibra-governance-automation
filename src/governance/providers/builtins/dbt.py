"""Built-in dbt Manifest source provider."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from governance import __version__ as package_version
from governance.domain.graph import GovernanceGraph
from governance.domain.observations import PropertyObservationSet
from governance.integrations.dbt import load_dbt_graph_with_observations
from governance.providers.builtins._common import (
    reject_unknown_keys,
    require_literal_relative_path,
    resolve_document_path,
)
from governance.providers.capabilities import CapabilityId
from governance.providers.contracts import (
    CapabilityBinding,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRuntimeContext,
)
from governance.providers.errors import CODE_INVALID_DESCRIPTOR, ProviderDiagnostic, ProviderError


class _DbtGraphCapability:
    def __init__(
        self,
        *,
        path: Path,
        namespace: str,
        default_database: str | None,
    ) -> None:
        self._path = path
        self._namespace = namespace
        self._default_database = default_database

    def load_graph(self) -> GovernanceGraph:
        return load_dbt_graph_with_observations(
            self._path,
            namespace=self._namespace,
            default_database=self._default_database,
        ).graph


class _DbtObservationsCapability:
    def __init__(
        self,
        *,
        path: Path,
        namespace: str,
        default_database: str | None,
    ) -> None:
        self._path = path
        self._namespace = namespace
        self._default_database = default_database

    def load_observations(self) -> PropertyObservationSet:
        return load_dbt_graph_with_observations(
            self._path,
            namespace=self._namespace,
            default_database=self._default_database,
        ).observations


_DBT_ALLOWED_KEYS = frozenset({"path", "namespace", "default_database"})


class _DbtConfigValidator:
    def validate(self, config: Mapping[str, object]) -> None:
        diagnostics: list[ProviderDiagnostic] = []
        diagnostics.extend(reject_unknown_keys(config, _DBT_ALLOWED_KEYS))
        try:
            require_literal_relative_path("path", config.get("path"), pointer="/path")
        except ProviderError as exc:
            diagnostics.extend(exc.errors)

        namespace = config.get("namespace")
        if not isinstance(namespace, str) or not namespace.strip():
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/namespace",
                    message="namespace must be a non-empty string",
                )
            )

        default_database = config.get("default_database")
        if default_database is not None and (
            not isinstance(default_database, str) or not default_database.strip()
        ):
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path="/default_database",
                    message="default_database must be a non-empty string when provided",
                )
            )

        if diagnostics:
            raise ProviderError(diagnostics)


def _resolved_manifest_path(context: ProviderRuntimeContext) -> Path:
    path = resolve_document_path(context, pointer="/path")
    assert isinstance(path, Path)
    return path


def _default_database(context: ProviderRuntimeContext) -> str | None:
    raw = context.config.get("default_database")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _graph_factory(context: ProviderRuntimeContext) -> _DbtGraphCapability:
    return _DbtGraphCapability(
        path=_resolved_manifest_path(context),
        namespace=str(context.config["namespace"]).strip(),
        default_database=_default_database(context),
    )


def _observations_factory(context: ProviderRuntimeContext) -> _DbtObservationsCapability:
    return _DbtObservationsCapability(
        path=_resolved_manifest_path(context),
        namespace=str(context.config["namespace"]).strip(),
        default_database=_default_database(context),
    )


def register() -> ProviderRegistration:
    descriptor = ProviderDescriptor(
        provider_id="dbt",
        display_name="dbt",
        provider_version=package_version,
        sdk_compatibility="==1",
        capabilities=(
            CapabilityId.GOVERNANCE_GRAPH,
            CapabilityId.PROPERTY_OBSERVATIONS,
        ),
    )
    return ProviderRegistration(
        descriptor=descriptor,
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.GOVERNANCE_GRAPH,
                factory=_graph_factory,
            ),
            CapabilityBinding(
                capability_id=CapabilityId.PROPERTY_OBSERVATIONS,
                factory=_observations_factory,
            ),
        ),
        config_validator=_DbtConfigValidator(),
    )
