"""Map validated OpenLineage core 2-0-2 events into GovernanceGraph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from governance.domain.graph import (
    EDGE_KIND_CONTAINS,
    EDGE_KIND_DEPENDS_ON,
    NODE_KIND_COLUMN,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    GovernanceGraph,
    GraphEdge,
    GraphNode,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.lineage import (
    ColumnLineageAssertion,
    materialize_column_lineage_edges,
)
from governance.domain.observations import (
    GovernanceMappingResult,
    PropertyObservationBuilder,
    PropertyPath,
)
from governance.identity.canonicalize import canonical_json_bytes
from governance.integrations.openlineage.errors import (
    CODE_MAPPING,
    OpenLineageDiagnostic,
    OpenLineageMappingError,
)
from governance.integrations.openlineage.load import load_openlineage_events
from governance.integrations.openlineage.validate import (
    SUPPORTED_DATASET_FACETS,
    validate_openlineage_events,
)

_ColumnLineageKey = tuple[str, str, str, str, str, str]

_RUN_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
_JOB_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/JobEvent"
_DATASET_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/DatasetEvent"

_PHYSICAL_HIERARCHY = ("DATABASE", "SCHEMA", "TABLE")


def map_openlineage_events(
    events: Sequence[Mapping[str, Any]],
    *,
    namespace: str,
) -> GovernanceGraph:
    """Validate and map OpenLineage events into a GovernanceGraph."""
    return map_openlineage_events_with_observations(events, namespace=namespace).graph


def map_openlineage_events_with_observations(
    events: Sequence[Mapping[str, Any]],
    *,
    namespace: str,
) -> GovernanceMappingResult:
    """Validate and map OpenLineage events into a graph with property observations."""
    ns = _require_namespace(namespace)
    validated = validate_openlineage_events(events)
    try:
        return _map_openlineage_result(validated, namespace=ns)
    except (TypeError, ValueError) as exc:
        raise OpenLineageMappingError(
            [
                OpenLineageDiagnostic(
                    code=CODE_MAPPING,
                    path="",
                    message="unable to build governance graph from OpenLineage events",
                )
            ]
        ) from exc


def load_openlineage_graph(path: str | Path, *, namespace: str) -> GovernanceGraph:
    """Load an OpenLineage JSON file and map it into a GovernanceGraph."""
    return load_openlineage_graph_with_observations(path, namespace=namespace).graph


def load_openlineage_graph_with_observations(
    path: str | Path, *, namespace: str
) -> GovernanceMappingResult:
    """Load an OpenLineage JSON file and map it with property observations."""
    ns = _require_namespace(namespace)
    events = load_openlineage_events(path)
    return map_openlineage_events_with_observations(events, namespace=ns)


def _require_namespace(namespace: object) -> str:
    if not isinstance(namespace, str) or not namespace.strip():
        raise OpenLineageMappingError(
            [
                OpenLineageDiagnostic(
                    code=CODE_MAPPING,
                    path="",
                    message="namespace is required",
                )
            ]
        )
    return namespace.strip()


def _mapping_error(path: str, message: str) -> OpenLineageMappingError:
    return OpenLineageMappingError(
        [
            OpenLineageDiagnostic(
                code=CODE_MAPPING,
                path=path,
                message=message,
            )
        ]
    )


def _pointer(parent: str, *parts: str | int) -> str:
    result = parent
    for part in parts:
        text = str(part).replace("~", "~0").replace("/", "~1")
        result = f"{result}/{text}" if result else f"/{text}"
    return result


def _norm_id(value: str) -> str:
    return value.strip()


def _dataset_provenance(
    producer: str, ol_ns: str, ol_name: str, schema_url: str
) -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type="openlineage",
        source_ref=canonical_json_bytes([producer, ol_ns, ol_name]).decode("utf-8"),
        source_version=schema_url,
        observation_mode="observed",
    )


def _facet_provenance(
    facet_producer: str,
    ol_ns: str,
    ol_name: str,
    facet_key: str,
    facet_schema_url: str,
) -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type="openlineage",
        source_ref=canonical_json_bytes([facet_producer, ol_ns, ol_name, facet_key]).decode(
            "utf-8"
        ),
        source_version=facet_schema_url,
        observation_mode="observed",
    )


def _edge_provenance(
    producer: str, job_ns: str, job_name: str, schema_url: str
) -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type="openlineage",
        source_ref=canonical_json_bytes([producer, job_ns, job_name]).decode("utf-8"),
        source_version=schema_url,
        observation_mode="observed",
    )


def _normalize_schema_fields(fields: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for field_item in fields:
        item: dict[str, Any] = {"name": _norm_id(str(field_item["name"]))}
        field_type = field_item.get("type")
        if isinstance(field_type, str) and field_type.strip():
            item["type"] = field_type
        description = field_item.get("description")
        if isinstance(description, str) and description.strip():
            item["description"] = description
        nested = field_item.get("fields")
        if isinstance(nested, list):
            item["fields"] = _normalize_schema_fields(nested)
        normalized.append(item)
    normalized.sort(key=lambda entry: entry["name"])
    return normalized


def _normalize_ownership(owners: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for owner in owners:
        name = _norm_id(str(owner["name"]))
        owner_type = _norm_id(str(owner["type"]))
        key = (name, owner_type)
        if key in seen:
            continue
        seen.add(key)
        items.append({"name": name, "type": owner_type})
    items.sort(key=lambda entry: (entry["name"], entry["type"]))
    return items


def _normalize_hierarchy(levels: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {"type": _norm_id(str(level["type"])), "name": _norm_id(str(level["name"]))}
        for level in levels
    ]


def _normalize_supported_facet(key: str, facet: Mapping[str, Any]) -> Any:
    if key == "schema":
        fields = facet.get("fields")
        if not isinstance(fields, list):
            return {"fields": []}
        return {"fields": _normalize_schema_fields(fields)}
    if key == "hierarchy":
        return {"hierarchy": _normalize_hierarchy(facet["hierarchy"])}
    if key == "datasetType":
        material: dict[str, Any] = {"datasetType": _norm_id(str(facet["datasetType"]))}
        subtype = facet.get("subType")
        if isinstance(subtype, str) and subtype.strip():
            material["subType"] = subtype.strip()
        return material
    if key == "storage":
        material = {"storageLayer": _norm_id(str(facet["storageLayer"]))}
        file_format = facet.get("fileFormat")
        if isinstance(file_format, str):
            material["fileFormat"] = file_format
        return material
    if key == "ownership":
        return {"owners": _normalize_ownership(facet["owners"])}
    raise _mapping_error("", f"unsupported facet key {key!r}")


def _facet_material_key(material: Any) -> bytes:
    return canonical_json_bytes(material)


def _is_physical_hierarchy(levels: Sequence[Mapping[str, str]]) -> bool:
    if len(levels) != 3:
        return False
    return tuple(level["type"] for level in levels) == _PHYSICAL_HIERARCHY and all(
        level["name"] for level in levels
    )


@dataclass
class _FacetState:
    material: Any
    provenances: list[ProvenanceRecord] = field(default_factory=list)


@dataclass
class _DatasetState:
    ol_ns: str
    ol_name: str
    dataset_provenances: list[ProvenanceRecord] = field(default_factory=list)
    facets: dict[str, _FacetState] = field(default_factory=dict)


@dataclass
class _RunState:
    job_ns: str
    job_name: str
    inputs: set[tuple[str, str]] = field(default_factory=set)
    outputs: set[tuple[str, str]] = field(default_factory=set)
    edge_provenances: list[ProvenanceRecord] = field(default_factory=list)


def _ol_key(dataset: Mapping[str, Any]) -> tuple[str, str]:
    return (_norm_id(str(dataset["namespace"])), _norm_id(str(dataset["name"])))


def _ingest_column_lineage_facet(
    states: dict[tuple[str, str], _DatasetState],
    registry: dict[_ColumnLineageKey, list[ProvenanceRecord]],
    dataset: Mapping[str, Any],
) -> None:
    """Collect columnLineage assertions without static-facet conflict semantics."""
    facets = dataset.get("facets")
    if not isinstance(facets, Mapping):
        return
    facet = facets.get("columnLineage")
    if not isinstance(facet, Mapping):
        return

    target_ns, target_name = _ol_key(dataset)
    facet_prov = _facet_provenance(
        str(facet["_producer"]),
        target_ns,
        target_name,
        "columnLineage",
        str(facet["_schemaURL"]),
    )
    fields = facet.get("fields")
    if not isinstance(fields, Mapping):
        return

    for output_field, entry in fields.items():
        if not isinstance(entry, Mapping):
            continue
        output_name = _norm_id(str(output_field))
        input_fields = entry.get("inputFields")
        if not isinstance(input_fields, list):
            continue
        for input_field in input_fields:
            if not isinstance(input_field, Mapping):
                continue
            input_ns = _norm_id(str(input_field["namespace"]))
            input_name = _norm_id(str(input_field["name"]))
            input_field_name = _norm_id(str(input_field["field"]))
            key: _ColumnLineageKey = (
                target_ns,
                target_name,
                output_name,
                input_ns,
                input_name,
                input_field_name,
            )
            bucket = registry.setdefault(key, [])
            if facet_prov not in bucket:
                bucket.append(facet_prov)

            input_key = (input_ns, input_name)
            input_state = states.get(input_key)
            if input_state is None:
                input_state = _DatasetState(ol_ns=input_ns, ol_name=input_name)
                states[input_key] = input_state
            if facet_prov not in input_state.dataset_provenances:
                input_state.dataset_provenances.append(facet_prov)


def _merge_dataset_observation(
    states: dict[tuple[str, str], _DatasetState],
    dataset: Mapping[str, Any],
    *,
    producer: str,
    schema_url: str,
    path: str,
    column_lineage: dict[_ColumnLineageKey, list[ProvenanceRecord]] | None = None,
) -> tuple[str, str]:
    key = _ol_key(dataset)
    ol_ns, ol_name = key
    state = states.get(key)
    if state is None:
        state = _DatasetState(ol_ns=ol_ns, ol_name=ol_name)
        states[key] = state

    dataset_prov = _dataset_provenance(producer, ol_ns, ol_name, schema_url)
    if dataset_prov not in state.dataset_provenances:
        state.dataset_provenances.append(dataset_prov)

    facets = dataset.get("facets")
    if not isinstance(facets, Mapping):
        if column_lineage is not None:
            _ingest_column_lineage_facet(states, column_lineage, dataset)
        return key

    for facet_key, facet in facets.items():
        if facet_key not in SUPPORTED_DATASET_FACETS:
            continue
        if not isinstance(facet, Mapping):
            continue
        material = _normalize_supported_facet(facet_key, facet)
        facet_prov = _facet_provenance(
            str(facet["_producer"]),
            ol_ns,
            ol_name,
            facet_key,
            str(facet["_schemaURL"]),
        )
        existing = state.facets.get(facet_key)
        if existing is None:
            state.facets[facet_key] = _FacetState(material=material, provenances=[facet_prov])
            continue
        if _facet_material_key(existing.material) != _facet_material_key(material):
            raise _mapping_error(
                _pointer(path, "facets", facet_key),
                "conflicting OpenLineage supported facet observations",
            )
        if facet_prov not in existing.provenances:
            existing.provenances.append(facet_prov)

    if column_lineage is not None:
        _ingest_column_lineage_facet(states, column_lineage, dataset)
    return key


def _collect_datasets_from_list(
    states: dict[tuple[str, str], _DatasetState],
    datasets: object,
    *,
    producer: str,
    schema_url: str,
    path: str,
    column_lineage: dict[_ColumnLineageKey, list[ProvenanceRecord]] | None = None,
) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    if not isinstance(datasets, list):
        return keys
    for index, dataset in enumerate(datasets):
        if not isinstance(dataset, Mapping):
            continue
        keys.add(
            _merge_dataset_observation(
                states,
                dataset,
                producer=producer,
                schema_url=schema_url,
                path=_pointer(path, index),
                column_lineage=column_lineage,
            )
        )
    return keys


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


def _dataset_attributes(state: _DatasetState) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    dataset_type = state.facets.get("datasetType")
    if dataset_type is not None:
        material = dataset_type.material
        attrs["dataset_type"] = material["datasetType"]
        if "subType" in material:
            attrs["dataset_subtype"] = material["subType"]

    storage = state.facets.get("storage")
    if storage is not None:
        material = storage.material
        attrs["storage_layer"] = material["storageLayer"]
        if "fileFormat" in material:
            attrs["file_format"] = material["fileFormat"]

    ownership = state.facets.get("ownership")
    if ownership is not None:
        attrs["ownership"] = ownership.material["owners"]

    hierarchy = state.facets.get("hierarchy")
    if hierarchy is not None:
        levels = hierarchy.material["hierarchy"]
        if not _is_physical_hierarchy(levels):
            attrs["hierarchy"] = levels
    return attrs


def _dataset_node_provenance(state: _DatasetState) -> tuple[ProvenanceRecord, ...]:
    """Union event and supported-facet provenance for the dataset/table node."""
    records: list[ProvenanceRecord] = list(state.dataset_provenances)
    for facet_key in sorted(state.facets.keys()):
        records.extend(state.facets[facet_key].provenances)
    return tuple(records)


def _observe_with_provenances(
    builder: PropertyObservationBuilder,
    identity: GraphNodeIdentity,
    property_path: PropertyPath,
    value: Any,
    provenances: Sequence[ProvenanceRecord],
) -> None:
    for provenance in provenances:
        builder.observe(identity, property_path, value, provenance)


def _observe_dataset_node(
    builder: PropertyObservationBuilder,
    *,
    identity: GraphNodeIdentity,
    display_name: str,
    attributes: Mapping[str, Any],
    state: _DatasetState,
) -> None:
    """Emit property observations with facet-exact provenance (never node union)."""
    hierarchy = state.facets.get("hierarchy")
    if hierarchy is not None and _is_physical_hierarchy(hierarchy.material["hierarchy"]):
        name_provenances = hierarchy.provenances
    else:
        name_provenances = state.dataset_provenances
    _observe_with_provenances(
        builder,
        identity,
        PropertyPath(("name",)),
        display_name,
        name_provenances,
    )

    dataset_type = state.facets.get("datasetType")
    if dataset_type is not None:
        for key in ("dataset_type", "dataset_subtype"):
            if key in attributes:
                _observe_with_provenances(
                    builder,
                    identity,
                    PropertyPath(("attributes", key)),
                    attributes[key],
                    dataset_type.provenances,
                )

    storage = state.facets.get("storage")
    if storage is not None:
        for key in ("storage_layer", "file_format"):
            if key in attributes:
                _observe_with_provenances(
                    builder,
                    identity,
                    PropertyPath(("attributes", key)),
                    attributes[key],
                    storage.provenances,
                )

    ownership = state.facets.get("ownership")
    if ownership is not None and "ownership" in attributes:
        _observe_with_provenances(
            builder,
            identity,
            PropertyPath(("attributes", "ownership")),
            attributes["ownership"],
            ownership.provenances,
        )

    if hierarchy is not None and "hierarchy" in attributes:
        _observe_with_provenances(
            builder,
            identity,
            PropertyPath(("attributes", "hierarchy")),
            attributes["hierarchy"],
            hierarchy.provenances,
        )


def _observe_column_node(
    builder: PropertyObservationBuilder,
    node: GraphNode,
    provenances: Sequence[ProvenanceRecord],
) -> None:
    """Emit schema/lineage column property observations with exact provenances."""
    if not provenances:
        return
    _observe_with_provenances(
        builder,
        node.identity,
        PropertyPath(("name",)),
        node.name,
        provenances,
    )
    if node.description is not None:
        _observe_with_provenances(
            builder,
            node.identity,
            PropertyPath(("description",)),
            node.description,
            provenances,
        )
    attributes = node.to_dict()["attributes"]
    assert isinstance(attributes, dict)
    for key, value in attributes.items():
        _observe_with_provenances(
            builder,
            node.identity,
            PropertyPath(("attributes", key)),
            value,
            provenances,
        )


def _resolve_identity(
    state: _DatasetState,
    *,
    namespace: str,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    seen_containers: set[GraphNodeIdentity],
) -> GraphNodeIdentity:
    hierarchy = state.facets.get("hierarchy")
    if hierarchy is not None:
        levels = hierarchy.material["hierarchy"]
        if _is_physical_hierarchy(levels):
            database = levels[0]["name"]
            schema = levels[1]["name"]
            table = levels[2]["name"]
            dataset_identity = _ensure_containers(
                namespace=namespace,
                database=database,
                schema=schema,
                nodes=nodes,
                edges=edges,
                seen_containers=seen_containers,
            )
            return GraphNodeIdentity(namespace, NODE_KIND_TABLE, table, parent=dataset_identity)

    logical_id = canonical_json_bytes([state.ol_ns, state.ol_name]).decode("utf-8")
    return GraphNodeIdentity(namespace, NODE_KIND_DATASET, logical_id)


def _map_schema_fields(
    fields: Sequence[Mapping[str, Any]],
    *,
    namespace: str,
    parent_identity: GraphNodeIdentity,
    provenances: Sequence[ProvenanceRecord],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    observations: PropertyObservationBuilder,
) -> None:
    for field_item in fields:
        name = str(field_item["name"])
        column_identity = GraphNodeIdentity(
            namespace, NODE_KIND_COLUMN, name, parent=parent_identity
        )
        attrs: dict[str, Any] = {}
        field_type = field_item.get("type")
        if isinstance(field_type, str) and field_type.strip():
            attrs["data_type"] = field_type
        description = field_item.get("description")
        if isinstance(description, str) and description.strip():
            desc: str | None = description
        else:
            desc = None
        column_node = GraphNode(
            identity=column_identity,
            name=name,
            description=desc,
            attributes=attrs,
            provenance=tuple(provenances),
        )
        nodes.append(column_node)
        _observe_column_node(observations, column_node, provenances)
        edges.append(
            GraphEdge(
                source=parent_identity,
                target=column_identity,
                kind=EDGE_KIND_CONTAINS,
                attributes={},
                provenance=(),
            )
        )
        nested = field_item.get("fields")
        if isinstance(nested, list) and nested:
            _map_schema_fields(
                nested,
                namespace=namespace,
                parent_identity=column_identity,
                provenances=provenances,
                nodes=nodes,
                edges=edges,
                observations=observations,
            )


def _emit_lineage_edges(
    *,
    inputs: set[tuple[str, str]],
    outputs: set[tuple[str, str]],
    identities: Mapping[tuple[str, str], GraphNodeIdentity],
    provenances: Sequence[ProvenanceRecord],
    edges: list[GraphEdge],
) -> None:
    if not inputs or not outputs:
        return
    for output_key in sorted(outputs):
        for input_key in sorted(inputs):
            edges.append(
                GraphEdge(
                    source=identities[output_key],
                    target=identities[input_key],
                    kind=EDGE_KIND_DEPENDS_ON,
                    attributes={},
                    provenance=tuple(provenances),
                )
            )


def _ensure_lineage_column(
    *,
    column_identity: GraphNodeIdentity,
    parent_identity: GraphNodeIdentity,
    provenances: Sequence[ProvenanceRecord],
    columns: dict[GraphNodeIdentity, GraphNode],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    observations: PropertyObservationBuilder,
) -> None:
    existing = columns.get(column_identity)
    if existing is None:
        node = GraphNode(
            identity=column_identity,
            name=column_identity.logical_id,
            description=None,
            attributes={},
            provenance=tuple(provenances),
        )
        nodes.append(node)
        columns[column_identity] = node
        # Lineage-only columns: observe /name with columnLineage provenances.
        _observe_column_node(observations, node, provenances)
        edges.append(
            GraphEdge(
                source=parent_identity,
                target=column_identity,
                kind=EDGE_KIND_CONTAINS,
                attributes={},
                provenance=(),
            )
        )
        return

    merged = GraphNode(
        identity=existing.identity,
        name=existing.name,
        description=existing.description,
        attributes=existing.to_dict()["attributes"],
        provenance=existing.provenance + tuple(provenances),
    )
    nodes.append(merged)
    columns[column_identity] = merged


def _emit_column_lineage_edges(
    *,
    namespace: str,
    registry: Mapping[_ColumnLineageKey, Sequence[ProvenanceRecord]],
    identities: Mapping[tuple[str, str], GraphNodeIdentity],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    observations: PropertyObservationBuilder,
) -> None:
    if not registry:
        return

    columns: dict[GraphNodeIdentity, GraphNode] = {
        node.identity: node for node in nodes if node.identity.kind == NODE_KIND_COLUMN
    }
    assertions: list[ColumnLineageAssertion] = []
    for key in sorted(registry.keys()):
        (
            target_ns,
            target_name,
            output_field,
            input_ns,
            input_name,
            input_field,
        ) = key
        provenances = tuple(registry[key])
        output_parent = identities[(target_ns, target_name)]
        input_parent = identities[(input_ns, input_name)]
        output_column = GraphNodeIdentity(
            namespace, NODE_KIND_COLUMN, output_field, parent=output_parent
        )
        input_column = GraphNodeIdentity(
            namespace, NODE_KIND_COLUMN, input_field, parent=input_parent
        )
        _ensure_lineage_column(
            column_identity=output_column,
            parent_identity=output_parent,
            provenances=provenances,
            columns=columns,
            nodes=nodes,
            edges=edges,
            observations=observations,
        )
        _ensure_lineage_column(
            column_identity=input_column,
            parent_identity=input_parent,
            provenances=provenances,
            columns=columns,
            nodes=nodes,
            edges=edges,
            observations=observations,
        )
        assertions.append(
            ColumnLineageAssertion(
                output_column=output_column,
                input_column=input_column,
                provenance=provenances,
            )
        )
    edges.extend(materialize_column_lineage_edges(assertions))


def _map_openlineage_result(
    events: Sequence[Mapping[str, Any]],
    *,
    namespace: str,
) -> GovernanceMappingResult:
    dataset_states: dict[tuple[str, str], _DatasetState] = {}
    column_lineage: dict[_ColumnLineageKey, list[ProvenanceRecord]] = {}
    run_states: dict[str, _RunState] = {}
    job_lineages: list[
        tuple[set[tuple[str, str]], set[tuple[str, str]], list[ProvenanceRecord]]
    ] = []

    for index, event in enumerate(events):
        path = _pointer("", index)
        schema_url = str(event["schemaURL"])
        producer = str(event["producer"])

        if schema_url == _DATASET_URL:
            dataset = event["dataset"]
            assert isinstance(dataset, Mapping)
            _merge_dataset_observation(
                dataset_states,
                dataset,
                producer=producer,
                schema_url=schema_url,
                path=_pointer(path, "dataset"),
                column_lineage=column_lineage,
            )
            continue

        if schema_url == _JOB_URL:
            job = event["job"]
            assert isinstance(job, Mapping)
            job_ns = _norm_id(str(job["namespace"]))
            job_name = _norm_id(str(job["name"]))
            inputs = _collect_datasets_from_list(
                dataset_states,
                event.get("inputs"),
                producer=producer,
                schema_url=schema_url,
                path=_pointer(path, "inputs"),
                column_lineage=column_lineage,
            )
            outputs = _collect_datasets_from_list(
                dataset_states,
                event.get("outputs"),
                producer=producer,
                schema_url=schema_url,
                path=_pointer(path, "outputs"),
                column_lineage=column_lineage,
            )
            job_lineages.append(
                (
                    inputs,
                    outputs,
                    [_edge_provenance(producer, job_ns, job_name, schema_url)],
                )
            )
            continue

        if schema_url == _RUN_URL:
            run = event["run"]
            job = event["job"]
            assert isinstance(run, Mapping)
            assert isinstance(job, Mapping)
            run_id = _norm_id(str(run["runId"]))
            job_ns = _norm_id(str(job["namespace"]))
            job_name = _norm_id(str(job["name"]))
            inputs = _collect_datasets_from_list(
                dataset_states,
                event.get("inputs"),
                producer=producer,
                schema_url=schema_url,
                path=_pointer(path, "inputs"),
                column_lineage=column_lineage,
            )
            outputs = _collect_datasets_from_list(
                dataset_states,
                event.get("outputs"),
                producer=producer,
                schema_url=schema_url,
                path=_pointer(path, "outputs"),
                column_lineage=column_lineage,
            )
            existing_run = run_states.get(run_id)
            edge_prov = _edge_provenance(producer, job_ns, job_name, schema_url)
            if existing_run is None:
                run_states[run_id] = _RunState(
                    job_ns=job_ns,
                    job_name=job_name,
                    inputs=set(inputs),
                    outputs=set(outputs),
                    edge_provenances=[edge_prov],
                )
            else:
                if existing_run.job_ns != job_ns or existing_run.job_name != job_name:
                    raise _mapping_error(
                        _pointer(path, "job"),
                        "run events disagree on job identity",
                    )
                existing_run.inputs.update(inputs)
                existing_run.outputs.update(outputs)
                if edge_prov not in existing_run.edge_provenances:
                    existing_run.edge_provenances.append(edge_prov)
            continue

        raise _mapping_error(_pointer(path, "schemaURL"), "unsupported event kind")

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    observations = PropertyObservationBuilder()
    seen_containers: set[GraphNodeIdentity] = set()
    identities: dict[tuple[str, str], GraphNodeIdentity] = {}

    for key in sorted(dataset_states.keys()):
        state = dataset_states[key]
        identity = _resolve_identity(
            state,
            namespace=namespace,
            nodes=nodes,
            edges=edges,
            seen_containers=seen_containers,
        )
        identities[key] = identity
        display_name = identity.logical_id if identity.kind == NODE_KIND_TABLE else state.ol_name
        attributes = _dataset_attributes(state)

        nodes.append(
            GraphNode(
                identity=identity,
                name=display_name,
                description=None,
                attributes=attributes,
                provenance=_dataset_node_provenance(state),
            )
        )
        _observe_dataset_node(
            observations,
            identity=identity,
            display_name=display_name,
            attributes=attributes,
            state=state,
        )

        if identity.kind == NODE_KIND_TABLE:
            parent_for_contains = identity.parent
            assert parent_for_contains is not None
            edges.append(
                GraphEdge(
                    source=parent_for_contains,
                    target=identity,
                    kind=EDGE_KIND_CONTAINS,
                    attributes={},
                    provenance=(),
                )
            )

        schema_facet = state.facets.get("schema")
        if schema_facet is not None:
            fields = schema_facet.material["fields"]
            if fields:
                _map_schema_fields(
                    fields,
                    namespace=namespace,
                    parent_identity=identity,
                    provenances=schema_facet.provenances,
                    nodes=nodes,
                    edges=edges,
                    observations=observations,
                )

    _emit_column_lineage_edges(
        namespace=namespace,
        registry=column_lineage,
        identities=identities,
        nodes=nodes,
        edges=edges,
        observations=observations,
    )

    for run_state in run_states.values():
        _emit_lineage_edges(
            inputs=run_state.inputs,
            outputs=run_state.outputs,
            identities=identities,
            provenances=run_state.edge_provenances,
            edges=edges,
        )

    for inputs, outputs, provenances in job_lineages:
        _emit_lineage_edges(
            inputs=inputs,
            outputs=outputs,
            identities=identities,
            provenances=provenances,
            edges=edges,
        )

    return GovernanceMappingResult(
        graph=GovernanceGraph.from_parts(nodes, edges),
        observations=observations.build(),
    )
