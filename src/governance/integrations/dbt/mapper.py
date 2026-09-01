"""Map validated dbt Manifest v12 subset documents into GovernanceGraph.

Reconciliation contract for physical relations (shared with a future PostgreSQL
graph converter): same ``namespace`` + database + schema + physical table
identifier yields the same :class:`GraphNodeIdentity` hierarchy

``data_source(database) -> dataset(schema) -> table(identifier) -> column(name)``.

Model physical table identifier is ``alias``; source physical table identifier
is ``identifier``. dbt ``unique_id`` is provenance only, never core identity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from governance.domain.graph import (
    EDGE_KIND_CONTAINS,
    EDGE_KIND_DEPENDS_ON,
    NODE_KIND_COLUMN,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    NODE_KIND_TRANSFORMATION,
    GovernanceGraph,
    GraphEdge,
    GraphNode,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.observations import (
    GovernanceMappingResult,
    PropertyObservationBuilder,
    PropertyPath,
)
from governance.identity.canonicalize import canonical_json_bytes
from governance.integrations.dbt.errors import (
    CODE_MAPPING,
    DbtDiagnostic,
    DbtMappingError,
)
from governance.integrations.dbt.load import load_dbt_manifest
from governance.integrations.dbt.validate import validate_dbt_manifest

_RESOURCE_TYPE_MODEL = "model"
_RESOURCE_TYPE_SOURCE = "source"
_RESOLVE_PREFIXES = frozenset({_RESOURCE_TYPE_MODEL, _RESOURCE_TYPE_SOURCE})


def map_dbt_manifest(
    document: Mapping[str, Any],
    *,
    namespace: str,
    default_database: str | None = None,
) -> GovernanceGraph:
    """Validate and map a dbt Manifest into a GovernanceGraph."""
    return map_dbt_manifest_with_observations(
        document, namespace=namespace, default_database=default_database
    ).graph


def map_dbt_manifest_with_observations(
    document: Mapping[str, Any],
    *,
    namespace: str,
    default_database: str | None = None,
) -> GovernanceMappingResult:
    """Validate and map a dbt Manifest into a graph with property observations."""
    ns = _require_namespace(namespace)
    default_db = _require_default_database(default_database)
    validated = validate_dbt_manifest(document)
    return _map_dbt_result(validated, namespace=ns, default_database=default_db)


def load_dbt_graph(
    path: str | Path,
    *,
    namespace: str,
    default_database: str | None = None,
) -> GovernanceGraph:
    """Load a dbt Manifest JSON file and map it into a GovernanceGraph."""
    return load_dbt_graph_with_observations(
        path, namespace=namespace, default_database=default_database
    ).graph


def load_dbt_graph_with_observations(
    path: str | Path,
    *,
    namespace: str,
    default_database: str | None = None,
) -> GovernanceMappingResult:
    """Load a dbt Manifest JSON file and map it with property observations."""
    ns = _require_namespace(namespace)
    default_db = _require_default_database(default_database)
    document = load_dbt_manifest(path)
    return map_dbt_manifest_with_observations(document, namespace=ns, default_database=default_db)


def _require_namespace(namespace: object) -> str:
    if not isinstance(namespace, str) or not namespace.strip():
        raise DbtMappingError(
            [
                DbtDiagnostic(
                    code=CODE_MAPPING,
                    path="",
                    message="namespace is required",
                )
            ]
        )
    return namespace.strip()


def _require_default_database(default_database: object) -> str | None:
    if default_database is None:
        return None
    if not isinstance(default_database, str) or not default_database.strip():
        raise DbtMappingError(
            [
                DbtDiagnostic(
                    code=CODE_MAPPING,
                    path="",
                    message="default_database must be a non-empty string when provided",
                )
            ]
        )
    return default_database.strip()


def _mapping_error(path: str, message: str) -> DbtMappingError:
    return DbtMappingError(
        [
            DbtDiagnostic(
                code=CODE_MAPPING,
                path=path,
                message=message,
            )
        ]
    )


def _normalize_description(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if not value.strip():
        return None
    return value


def _normalize_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    strings = [item for item in value if isinstance(item, str)]
    return sorted(set(strings))


def _normalize_constraints(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items = [dict(item) for item in value if isinstance(item, Mapping)]
    unique: dict[bytes, dict[str, Any]] = {}
    for item in items:
        unique[canonical_json_bytes(item)] = item
    return [unique[key] for key in sorted(unique.keys())]


def _dbt_provenance(unique_id: str, dbt_version: str) -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type="dbt",
        source_ref=unique_id,
        source_version=dbt_version,
        observation_mode="declared",
    )


def _observe_node(
    builder: PropertyObservationBuilder,
    node: GraphNode,
    provenance: ProvenanceRecord,
) -> None:
    """Emit top-level property observations for a provenanced dbt node."""
    if not node.provenance:
        return
    builder.observe(node.identity, PropertyPath(("name",)), node.name, provenance)
    if node.description is not None:
        builder.observe(
            node.identity,
            PropertyPath(("description",)),
            node.description,
            provenance,
        )
    attributes = node.to_dict()["attributes"]
    assert isinstance(attributes, dict)
    for key, value in attributes.items():
        builder.observe(
            node.identity,
            PropertyPath(("attributes", key)),
            value,
            provenance,
        )


def _effective_database(
    resource: Mapping[str, Any],
    *,
    path: str,
    default_database: str | None,
) -> str:
    database = resource.get("database")
    if isinstance(database, str) and database.strip():
        return database
    if default_database is not None:
        return default_database
    raise _mapping_error(
        _pointer(path, "database"),
        "database is required for relation-backed resources when default_database is not set",
    )


def _pointer(parent: str, *parts: str | int) -> str:
    result = parent
    for part in parts:
        text = str(part).replace("~", "~0").replace("/", "~1")
        result = f"{result}/{text}" if result else f"/{text}"
    return result


def _unique_id_prefix(unique_id: str) -> str:
    return unique_id.split(".", 1)[0]


def _is_ephemeral(model: Mapping[str, Any]) -> bool:
    config = model.get("config")
    if not isinstance(config, Mapping):
        return False
    return config.get("materialized") == "ephemeral"


def _transformation_logical_id(package_name: str, fqn: Sequence[str]) -> str:
    return canonical_json_bytes([package_name, list(fqn)]).decode("utf-8")


def _ensure_containers(
    *,
    namespace: str,
    database: str,
    schema: str,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    seen_containers: set[GraphNodeIdentity],
) -> GraphNodeIdentity:
    ds_identity = GraphNodeIdentity(namespace, NODE_KIND_DATA_SOURCE, database)
    if ds_identity not in seen_containers:
        seen_containers.add(ds_identity)
        nodes.append(
            GraphNode(
                identity=ds_identity,
                name=database,
                description=None,
                attributes={},
                provenance=(),
            )
        )

    dataset_identity = GraphNodeIdentity(namespace, NODE_KIND_DATASET, schema, parent=ds_identity)
    if dataset_identity not in seen_containers:
        seen_containers.add(dataset_identity)
        nodes.append(
            GraphNode(
                identity=dataset_identity,
                name=schema,
                description=None,
                attributes={},
                provenance=(),
            )
        )
        edges.append(
            GraphEdge(
                source=ds_identity,
                target=dataset_identity,
                kind=EDGE_KIND_CONTAINS,
                attributes={},
                provenance=(),
            )
        )
    return dataset_identity


def _common_resource_attrs(resource: Mapping[str, Any], *, resource_type: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "dbt_resource_type": resource_type,
        "dbt_name": resource["name"],
        "package_name": resource["package_name"],
    }
    if "fqn" in resource and isinstance(resource["fqn"], list):
        attrs["fqn"] = list(resource["fqn"])
    tags = _normalize_tags(resource.get("tags", []))
    if tags:
        attrs["tags"] = tags
    meta = resource.get("meta")
    if isinstance(meta, Mapping) and meta:
        attrs["meta"] = dict(meta)
    return attrs


def _model_attributes(model: Mapping[str, Any]) -> dict[str, Any]:
    attrs = _common_resource_attrs(model, resource_type=_RESOURCE_TYPE_MODEL)
    config = model.get("config")
    if isinstance(config, Mapping):
        materialized = config.get("materialized")
        if isinstance(materialized, str) and materialized:
            attrs["materialized"] = materialized
    return attrs


def _source_attributes(source: Mapping[str, Any]) -> dict[str, Any]:
    attrs = _common_resource_attrs(source, resource_type=_RESOURCE_TYPE_SOURCE)
    attrs["source_name"] = source["source_name"]
    loader = source.get("loader")
    if isinstance(loader, str) and loader.strip():
        attrs["loader"] = loader
    return attrs


def _column_attributes(column: Mapping[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if "data_type" in column and column["data_type"] is not None:
        attrs["data_type"] = column["data_type"]
    tags = _normalize_tags(column.get("tags", []))
    if tags:
        attrs["tags"] = tags
    meta = column.get("meta")
    if isinstance(meta, Mapping) and meta:
        attrs["meta"] = dict(meta)
    if "quote" in column and column["quote"] is not None:
        attrs["quote"] = column["quote"]
    if "constraints" in column:
        constraints = _normalize_constraints(column["constraints"])
        if constraints:
            attrs["constraints"] = constraints
    return attrs


def _map_columns(
    columns: object,
    *,
    namespace: str,
    parent_identity: GraphNodeIdentity,
    provenance: ProvenanceRecord,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    observations: PropertyObservationBuilder,
) -> None:
    if not isinstance(columns, Mapping):
        return
    for column in columns.values():
        if not isinstance(column, Mapping):
            continue
        name = column["name"]
        assert isinstance(name, str)
        column_identity = GraphNodeIdentity(
            namespace, NODE_KIND_COLUMN, name, parent=parent_identity
        )
        column_node = GraphNode(
            identity=column_identity,
            name=name,
            description=_normalize_description(column.get("description")),
            attributes=_column_attributes(column),
            provenance=(provenance,),
        )
        nodes.append(column_node)
        _observe_node(observations, column_node, provenance)
        edges.append(
            GraphEdge(
                source=parent_identity,
                target=column_identity,
                kind=EDGE_KIND_CONTAINS,
                attributes={},
                provenance=(),
            )
        )


def _map_model(
    model: Mapping[str, Any],
    *,
    namespace: str,
    default_database: str | None,
    dbt_version: str,
    path: str,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    observations: PropertyObservationBuilder,
    seen_containers: set[GraphNodeIdentity],
    registry: dict[str, GraphNodeIdentity],
) -> None:
    unique_id = model["unique_id"]
    assert isinstance(unique_id, str)
    provenance = _dbt_provenance(unique_id, dbt_version)

    if _is_ephemeral(model):
        package_name = model["package_name"]
        fqn = model["fqn"]
        assert isinstance(package_name, str)
        assert isinstance(fqn, list)
        logical_id = _transformation_logical_id(package_name, fqn)
        identity = GraphNodeIdentity(namespace, NODE_KIND_TRANSFORMATION, logical_id)
        transform_node = GraphNode(
            identity=identity,
            name=model["name"],
            description=_normalize_description(model.get("description")),
            attributes=_model_attributes(model),
            provenance=(provenance,),
        )
        nodes.append(transform_node)
        _observe_node(observations, transform_node, provenance)
        registry[unique_id] = identity
        _map_columns(
            model.get("columns"),
            namespace=namespace,
            parent_identity=identity,
            provenance=provenance,
            nodes=nodes,
            edges=edges,
            observations=observations,
        )
        return

    database = _effective_database(model, path=path, default_database=default_database)
    schema = model["schema"]
    alias = model["alias"]
    assert isinstance(schema, str)
    assert isinstance(alias, str)
    dataset_identity = _ensure_containers(
        namespace=namespace,
        database=database,
        schema=schema,
        nodes=nodes,
        edges=edges,
        seen_containers=seen_containers,
    )
    table_identity = GraphNodeIdentity(namespace, NODE_KIND_TABLE, alias, parent=dataset_identity)
    table_node = GraphNode(
        identity=table_identity,
        name=alias,
        description=_normalize_description(model.get("description")),
        attributes=_model_attributes(model),
        provenance=(provenance,),
    )
    nodes.append(table_node)
    _observe_node(observations, table_node, provenance)
    edges.append(
        GraphEdge(
            source=dataset_identity,
            target=table_identity,
            kind=EDGE_KIND_CONTAINS,
            attributes={},
            provenance=(),
        )
    )
    registry[unique_id] = table_identity
    _map_columns(
        model.get("columns"),
        namespace=namespace,
        parent_identity=table_identity,
        provenance=provenance,
        nodes=nodes,
        edges=edges,
        observations=observations,
    )


def _map_source(
    source: Mapping[str, Any],
    *,
    namespace: str,
    default_database: str | None,
    dbt_version: str,
    path: str,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    observations: PropertyObservationBuilder,
    seen_containers: set[GraphNodeIdentity],
    registry: dict[str, GraphNodeIdentity],
) -> None:
    unique_id = source["unique_id"]
    assert isinstance(unique_id, str)
    provenance = _dbt_provenance(unique_id, dbt_version)
    database = _effective_database(source, path=path, default_database=default_database)
    schema = source["schema"]
    identifier = source["identifier"]
    assert isinstance(schema, str)
    assert isinstance(identifier, str)
    dataset_identity = _ensure_containers(
        namespace=namespace,
        database=database,
        schema=schema,
        nodes=nodes,
        edges=edges,
        seen_containers=seen_containers,
    )
    table_identity = GraphNodeIdentity(
        namespace, NODE_KIND_TABLE, identifier, parent=dataset_identity
    )
    table_node = GraphNode(
        identity=table_identity,
        name=identifier,
        description=_normalize_description(source.get("description")),
        attributes=_source_attributes(source),
        provenance=(provenance,),
    )
    nodes.append(table_node)
    _observe_node(observations, table_node, provenance)
    edges.append(
        GraphEdge(
            source=dataset_identity,
            target=table_identity,
            kind=EDGE_KIND_CONTAINS,
            attributes={},
            provenance=(),
        )
    )
    registry[unique_id] = table_identity
    _map_columns(
        source.get("columns"),
        namespace=namespace,
        parent_identity=table_identity,
        provenance=provenance,
        nodes=nodes,
        edges=edges,
        observations=observations,
    )


def _map_dependencies(
    *,
    parent_map: Mapping[str, Any],
    registry: Mapping[str, GraphNodeIdentity],
    disabled_ids: set[str],
    dbt_version: str,
    edges: list[GraphEdge],
) -> None:
    for child_uid in sorted(registry.keys()):
        parents = parent_map.get(child_uid)
        if parents is None:
            continue
        if not isinstance(parents, list):
            continue
        deduped = sorted({ref for ref in parents if isinstance(ref, str) and ref})
        child_identity = registry[child_uid]
        child_provenance = _dbt_provenance(child_uid, dbt_version)
        for parent_uid in deduped:
            if parent_uid in registry:
                edges.append(
                    GraphEdge(
                        source=child_identity,
                        target=registry[parent_uid],
                        kind=EDGE_KIND_DEPENDS_ON,
                        attributes={},
                        provenance=(child_provenance,),
                    )
                )
                continue
            if parent_uid in disabled_ids:
                continue
            prefix = _unique_id_prefix(parent_uid)
            if prefix in _RESOLVE_PREFIXES:
                raise _mapping_error(
                    _pointer("/parent_map", child_uid),
                    "parent_map references unresolved model or source unique_id",
                )
            # Unsupported / unknown resource types are omitted deterministically.


def _map_dbt_result(
    document: Mapping[str, Any],
    *,
    namespace: str,
    default_database: str | None,
) -> GovernanceMappingResult:
    metadata = document["metadata"]
    assert isinstance(metadata, Mapping)
    dbt_version = metadata["dbt_version"]
    assert isinstance(dbt_version, str)

    graph_nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    observations = PropertyObservationBuilder()
    registry: dict[str, GraphNodeIdentity] = {}
    seen_containers: set[GraphNodeIdentity] = set()

    nodes = document["nodes"]
    assert isinstance(nodes, Mapping)
    # Deterministic iteration for stable intermediate construction order.
    for unique_id in sorted(nodes.keys()):
        node = nodes[unique_id]
        if not isinstance(node, Mapping):
            continue
        if node.get("resource_type") != _RESOURCE_TYPE_MODEL:
            continue
        _map_model(
            node,
            namespace=namespace,
            default_database=default_database,
            dbt_version=dbt_version,
            path=_pointer("/nodes", unique_id),
            nodes=graph_nodes,
            edges=edges,
            observations=observations,
            seen_containers=seen_containers,
            registry=registry,
        )

    sources = document["sources"]
    assert isinstance(sources, Mapping)
    for unique_id in sorted(sources.keys()):
        source = sources[unique_id]
        if not isinstance(source, Mapping):
            continue
        if source.get("resource_type") != _RESOURCE_TYPE_SOURCE:
            continue
        _map_source(
            source,
            namespace=namespace,
            default_database=default_database,
            dbt_version=dbt_version,
            path=_pointer("/sources", unique_id),
            nodes=graph_nodes,
            edges=edges,
            observations=observations,
            seen_containers=seen_containers,
            registry=registry,
        )

    disabled = document["disabled"]
    assert isinstance(disabled, Mapping)
    disabled_ids = {
        key
        for key, entries in disabled.items()
        if isinstance(key, str) and isinstance(entries, list) and entries
    }

    parent_map = document["parent_map"]
    assert isinstance(parent_map, Mapping)
    _map_dependencies(
        parent_map=parent_map,
        registry=registry,
        disabled_ids=disabled_ids,
        dbt_version=dbt_version,
        edges=edges,
    )

    try:
        return GovernanceMappingResult(
            graph=GovernanceGraph.from_parts(graph_nodes, edges),
            observations=observations.build(),
        )
    except DbtMappingError:
        raise
    except (TypeError, ValueError) as exc:
        raise DbtMappingError(
            [
                DbtDiagnostic(
                    code=CODE_MAPPING,
                    path="",
                    message="failed to construct governance graph from dbt Manifest",
                )
            ]
        ) from exc
