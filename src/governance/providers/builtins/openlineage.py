"""Built-in OpenLineage source provider."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from governance import __version__ as package_version
from governance.domain.graph import GovernanceGraph
from governance.domain.lineage import ColumnLineageAssertion
from governance.domain.observations import PropertyObservationSet
from governance.integrations.openlineage.mapper import (
    load_openlineage_graph_with_observations,
    load_openlineage_lineage,
)
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


class _OpenLineageGraphCapability:
    def __init__(self, *, path: Path, namespace: str) -> None:
        self._path = path
        self._namespace = namespace

    def load_graph(self) -> GovernanceGraph:
        return load_openlineage_graph_with_observations(self._path, namespace=self._namespace).graph


class _OpenLineageObservationsCapability:
    def __init__(self, *, path: Path, namespace: str) -> None:
        self._path = path
        self._namespace = namespace

    def load_observations(self) -> PropertyObservationSet:
        return load_openlineage_graph_with_observations(
            self._path, namespace=self._namespace
        ).observations


class _OpenLineageLineageCapability:
    def __init__(self, *, path: Path, namespace: str) -> None:
        self._path = path
        self._namespace = namespace

    def load_lineage(self) -> Sequence[ColumnLineageAssertion]:
        return load_openlineage_lineage(self._path, namespace=self._namespace)


_OPENLINEAGE_ALLOWED_KEYS = frozenset({"path", "namespace"})


class _OpenLineageConfigValidator:
    def validate(self, config: Mapping[str, object]) -> None:
        diagnostics: list[ProviderDiagnostic] = []
        diagnostics.extend(reject_unknown_keys(config, _OPENLINEAGE_ALLOWED_KEYS))
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

        if diagnostics:
            raise ProviderError(diagnostics)


def _resolved_events_path(context: ProviderRuntimeContext) -> Path:
    path = resolve_document_path(context, pointer="/path")
    assert isinstance(path, Path)
    return path


def _graph_factory(context: ProviderRuntimeContext) -> _OpenLineageGraphCapability:
    namespace = str(context.config["namespace"]).strip()
    return _OpenLineageGraphCapability(path=_resolved_events_path(context), namespace=namespace)


def _observations_factory(context: ProviderRuntimeContext) -> _OpenLineageObservationsCapability:
    namespace = str(context.config["namespace"]).strip()
    return _OpenLineageObservationsCapability(
        path=_resolved_events_path(context),
        namespace=namespace,
    )


def _lineage_factory(context: ProviderRuntimeContext) -> _OpenLineageLineageCapability:
    namespace = str(context.config["namespace"]).strip()
    return _OpenLineageLineageCapability(
        path=_resolved_events_path(context),
        namespace=namespace,
    )


def register() -> ProviderRegistration:
    descriptor = ProviderDescriptor(
        provider_id="openlineage",
        display_name="OpenLineage",
        provider_version=package_version,
        sdk_compatibility="==1",
        capabilities=(
            CapabilityId.GOVERNANCE_GRAPH,
            CapabilityId.PROPERTY_OBSERVATIONS,
            CapabilityId.LINEAGE,
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
            CapabilityBinding(
                capability_id=CapabilityId.LINEAGE,
                factory=_lineage_factory,
            ),
        ),
        config_validator=_OpenLineageConfigValidator(),
    )
