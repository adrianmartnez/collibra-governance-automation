"""example.catalog — read-only governance_graph + property_observations provider."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from governance.domain.graph import (
    NODE_KIND_DATASET,
    GovernanceGraph,
    GraphNode,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.observations import (
    PropertyObservation,
    PropertyObservationSet,
    PropertyPath,
)
from governance.providers import (
    CapabilityBinding,
    CapabilityId,
    ProviderDescriptor,
    ProviderDiagnostic,
    ProviderError,
    ProviderRegistration,
    ProviderRuntimeContext,
)


def _require_relative_path(raw: object, *, pointer: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code="invalid_config",
                    path=pointer,
                    message="path must be a non-empty string",
                )
            ]
        )
    path = raw.strip().replace("\\", "/")
    if path.startswith("/") or (len(path) >= 2 and path[1] == ":"):
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code="invalid_config",
                    path=pointer,
                    message="path must be config-root-relative (not absolute)",
                )
            ]
        )
    if ".." in Path(path).parts:
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code="invalid_config",
                    path=pointer,
                    message="path must not contain '..' segments",
                )
            ]
        )
    return path


class _CatalogConfigValidator:
    def validate(self, config: Mapping[str, object]) -> None:
        diagnostics: list[ProviderDiagnostic] = []
        unknown = sorted(set(config) - {"path", "namespace"})
        for key in unknown:
            diagnostics.append(
                ProviderDiagnostic(
                    code="invalid_config",
                    path=f"/{key}",
                    message=f"unknown config key {key!r}",
                )
            )
        try:
            _require_relative_path(config.get("path"), pointer="/path")
        except ProviderError as exc:
            diagnostics.extend(exc.errors)
        namespace = config.get("namespace")
        if not isinstance(namespace, str) or not namespace.strip():
            diagnostics.append(
                ProviderDiagnostic(
                    code="invalid_config",
                    path="/namespace",
                    message="namespace must be a non-empty string",
                )
            )
        if diagnostics:
            raise ProviderError(diagnostics)


def _resolve_catalog_path(context: ProviderRuntimeContext) -> Path:
    relative = str(context.config["path"]).strip().replace("\\", "/")
    if context.config_root is None:
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code="invalid_config",
                    path="/path",
                    message="config_root is required to resolve catalog path",
                )
            ]
        )
    root = Path(context.config_root)
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code="invalid_config",
                    path="/path",
                    message="resolved path escapes config_root",
                )
            ]
        ) from exc
    return candidate


def _load_document(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code="invalid_catalog",
                    path="/path",
                    message="catalog root must be a JSON object",
                )
            ]
        )
    return payload


def _provenance(path: Path) -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type="example.catalog",
        source_ref=path.name,
        observation_mode="declared",
    )


def _build_graph(document: Mapping[str, Any], *, namespace: str, path: Path) -> GovernanceGraph:
    datasets = document.get("datasets")
    if not isinstance(datasets, list):
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code="invalid_catalog",
                    path="/datasets",
                    message="catalog.datasets must be a list",
                )
            ]
        )
    nodes: list[GraphNode] = []
    for index, item in enumerate(datasets):
        if not isinstance(item, dict):
            raise ProviderError(
                [
                    ProviderDiagnostic(
                        code="invalid_catalog",
                        path=f"/datasets/{index}",
                        message="dataset entries must be objects",
                    )
                ]
            )
        logical_id = item.get("id")
        name = item.get("name", logical_id)
        if not isinstance(logical_id, str) or not logical_id.strip():
            raise ProviderError(
                [
                    ProviderDiagnostic(
                        code="invalid_catalog",
                        path=f"/datasets/{index}/id",
                        message="dataset.id must be a non-empty string",
                    )
                ]
            )
        if not isinstance(name, str) or not name.strip():
            raise ProviderError(
                [
                    ProviderDiagnostic(
                        code="invalid_catalog",
                        path=f"/datasets/{index}/name",
                        message="dataset.name must be a non-empty string",
                    )
                ]
            )
        identity = GraphNodeIdentity(
            namespace=namespace,
            kind=NODE_KIND_DATASET,
            logical_id=logical_id.strip(),
        )
        nodes.append(
            GraphNode(
                identity=identity,
                name=name.strip(),
                description=(
                    item.get("description") if isinstance(item.get("description"), str) else None
                ),
                provenance=(_provenance(path),),
            )
        )
    return GovernanceGraph.from_parts(nodes, ())


def _build_observations(
    document: Mapping[str, Any],
    *,
    namespace: str,
    path: Path,
) -> PropertyObservationSet:
    datasets = document.get("datasets")
    if not isinstance(datasets, list):
        return PropertyObservationSet()
    observations: list[PropertyObservation] = []
    for item in datasets:
        if not isinstance(item, dict):
            continue
        logical_id = item.get("id")
        name = item.get("name", logical_id)
        if not isinstance(logical_id, str) or not logical_id.strip():
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        identity = GraphNodeIdentity(
            namespace=namespace,
            kind=NODE_KIND_DATASET,
            logical_id=logical_id.strip(),
        )
        observations.append(
            PropertyObservation(
                object_identity=identity,
                property_path=PropertyPath.parse("/name"),
                value=name.strip(),
                provenance=(_provenance(path),),
            )
        )
        description = item.get("description")
        if isinstance(description, str) and description.strip():
            observations.append(
                PropertyObservation(
                    object_identity=identity,
                    property_path=PropertyPath.parse("/description"),
                    value=description.strip(),
                    provenance=(_provenance(path),),
                )
            )
    return PropertyObservationSet.from_observations(observations)


class _CatalogGraphCapability:
    def __init__(self, *, path: Path, namespace: str) -> None:
        self._path = path
        self._namespace = namespace

    def load_graph(self) -> GovernanceGraph:
        return _build_graph(
            _load_document(self._path),
            namespace=self._namespace,
            path=self._path,
        )


class _CatalogObservationsCapability:
    def __init__(self, *, path: Path, namespace: str) -> None:
        self._path = path
        self._namespace = namespace

    def load_observations(self) -> PropertyObservationSet:
        return _build_observations(
            _load_document(self._path),
            namespace=self._namespace,
            path=self._path,
        )


def _graph_factory(context: ProviderRuntimeContext) -> _CatalogGraphCapability:
    path = _resolve_catalog_path(context)
    namespace = str(context.config["namespace"]).strip()
    return _CatalogGraphCapability(path=path, namespace=namespace)


def _observations_factory(context: ProviderRuntimeContext) -> _CatalogObservationsCapability:
    path = _resolve_catalog_path(context)
    namespace = str(context.config["namespace"]).strip()
    return _CatalogObservationsCapability(path=path, namespace=namespace)


def register() -> ProviderRegistration:
    """Zero-argument entry-point callable for group governance.providers."""
    return ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="example.catalog",
            display_name="Example Catalog Provider",
            provider_version="0.1.0",
            sdk_compatibility=">=1,<2",
            capabilities=(
                CapabilityId.GOVERNANCE_GRAPH,
                CapabilityId.PROPERTY_OBSERVATIONS,
            ),
        ),
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
        config_validator=_CatalogConfigValidator(),
    )
