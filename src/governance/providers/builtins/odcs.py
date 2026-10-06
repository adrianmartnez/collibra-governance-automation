"""Built-in ODCS source provider."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from governance import __version__ as package_version
from governance.domain.graph import GovernanceGraph
from governance.domain.observations import PropertyObservationSet
from governance.integrations.odcs import load_odcs_graph_with_observations
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


class _OdcsGraphCapability:
    def __init__(self, *, path: Path, namespace: str) -> None:
        self._path = path
        self._namespace = namespace

    def load_graph(self) -> GovernanceGraph:
        return load_odcs_graph_with_observations(self._path, namespace=self._namespace).graph


class _OdcsObservationsCapability:
    def __init__(self, *, path: Path, namespace: str) -> None:
        self._path = path
        self._namespace = namespace

    def load_observations(self) -> PropertyObservationSet:
        return load_odcs_graph_with_observations(self._path, namespace=self._namespace).observations


_ODCS_ALLOWED_KEYS = frozenset({"path", "namespace"})


class _OdcsConfigValidator:
    def validate(self, config: Mapping[str, object]) -> None:
        diagnostics: list[ProviderDiagnostic] = []
        diagnostics.extend(reject_unknown_keys(config, _ODCS_ALLOWED_KEYS))
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


def _resolved_document_path(context: ProviderRuntimeContext) -> Path:
    path = resolve_document_path(context, pointer="/path")
    assert isinstance(path, Path)
    return path


def _graph_factory(context: ProviderRuntimeContext) -> _OdcsGraphCapability:
    namespace = str(context.config["namespace"]).strip()
    return _OdcsGraphCapability(path=_resolved_document_path(context), namespace=namespace)


def _observations_factory(context: ProviderRuntimeContext) -> _OdcsObservationsCapability:
    namespace = str(context.config["namespace"]).strip()
    return _OdcsObservationsCapability(
        path=_resolved_document_path(context),
        namespace=namespace,
    )


def register() -> ProviderRegistration:
    descriptor = ProviderDescriptor(
        provider_id="odcs",
        display_name="Open Data Contract Standard (ODCS)",
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
        config_validator=_OdcsConfigValidator(),
    )
