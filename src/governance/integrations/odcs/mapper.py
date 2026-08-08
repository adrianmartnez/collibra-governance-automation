"""Map validated ODCS v3.1.0 documents into GovernanceGraph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from governance.domain.graph import (
    EDGE_KIND_CONTAINS,
    EDGE_KIND_GOVERNS,
    NODE_KIND_COLUMN,
    NODE_KIND_CONTRACT,
    NODE_KIND_DATASET,
    GovernanceGraph,
    GraphEdge,
    GraphNode,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.identity.canonicalize import canonical_json_bytes
from governance.integrations.odcs.errors import (
    CODE_MAPPING,
    OdcsDiagnostic,
    OdcsMappingError,
)
from governance.integrations.odcs.load import load_odcs_document
from governance.integrations.odcs.schema import validate_odcs_document


def map_odcs_document(document: Mapping[str, Any], *, namespace: str) -> GovernanceGraph:
    """Validate and map an ODCS document into a GovernanceGraph."""
    ns = _require_namespace(namespace)
    validated = validate_odcs_document(document)
    _assert_sibling_identity_uniqueness(validated)
    return _build_graph(validated, namespace=ns)


def load_odcs_graph(path: str | Path, *, namespace: str) -> GovernanceGraph:
    """Load an ODCS file and map it into a GovernanceGraph."""
    ns = _require_namespace(namespace)
    document = load_odcs_document(path)
    return map_odcs_document(document, namespace=ns)


def _require_namespace(namespace: object) -> str:
    if not isinstance(namespace, str) or not namespace.strip():
        raise OdcsMappingError(
            [
                OdcsDiagnostic(
                    code=CODE_MAPPING,
                    path="",
                    message="namespace is required",
                )
            ]
        )
    return namespace.strip()


def _mapping_error(path: str, message: str) -> OdcsMappingError:
    return OdcsMappingError(
        [
            OdcsDiagnostic(
                code=CODE_MAPPING,
                path=path,
                message=message,
            )
        ]
    )


def _require_mapped_str(value: object, *, path: str, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _mapping_error(path, message)
    return value.strip()


def _logical_identity(entry: Mapping[str, Any]) -> str:
    entry_id = entry.get("id")
    if isinstance(entry_id, str) and entry_id.strip():
        return entry_id.strip()
    name = entry.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return ""


def _assert_unique_siblings(
    entries: Sequence[Mapping[str, Any]],
    *,
    collection_path: str,
) -> None:
    seen: dict[str, int] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        logical_id = _logical_identity(entry)
        if not logical_id:
            continue
        if logical_id in seen:
            raise _mapping_error(
                f"{collection_path}/{index}",
                "duplicate logical identity within sibling collection",
            )
        seen[logical_id] = index


def _assert_items_property_siblings(items: object, *, items_path: str) -> None:
    """Walk nested ``items`` chains and validate sibling uniqueness of named properties."""
    current = items
    path = items_path
    while isinstance(current, Mapping):
        props = current.get("properties")
        if isinstance(props, list):
            _assert_property_siblings(props, base_path=f"{path}/properties")
        current = current.get("items")
        path = f"{path}/items"


def _assert_property_siblings(properties: object, *, base_path: str) -> None:
    if not isinstance(properties, list):
        return
    mappings = [item for item in properties if isinstance(item, Mapping)]
    _assert_unique_siblings(mappings, collection_path=base_path)
    for index, prop in enumerate(properties):
        if not isinstance(prop, Mapping):
            continue
        nested = prop.get("properties")
        _assert_property_siblings(nested, base_path=f"{base_path}/{index}/properties")
        _assert_items_property_siblings(
            prop.get("items"),
            items_path=f"{base_path}/{index}/items",
        )


def _assert_sibling_identity_uniqueness(document: Mapping[str, Any]) -> None:
    schema_entries = document.get("schema")
    if not isinstance(schema_entries, list):
        return
    mappings = [item for item in schema_entries if isinstance(item, Mapping)]
    _assert_unique_siblings(mappings, collection_path="/schema")
    for index, schema_obj in enumerate(schema_entries):
        if not isinstance(schema_obj, Mapping):
            continue
        _assert_property_siblings(
            schema_obj.get("properties"),
            base_path=f"/schema/{index}/properties",
        )


def _sort_by_canonical(items: Sequence[Any]) -> list[Any]:
    return sorted(items, key=lambda item: canonical_json_bytes(item))


def _normalize_tags(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    strings = [item for item in value if isinstance(item, str)]
    unique = sorted(set(strings))
    return unique


def _normalize_object_list(value: object) -> list[Any] | None:
    if not isinstance(value, list):
        return None
    return _sort_by_canonical(list(value))


def _normalize_custom_properties(value: object) -> list[Any] | None:
    """Sort the outer customProperties list only; leave value arrays untouched."""
    if not isinstance(value, list):
        return None
    return _sort_by_canonical(list(value))


def _normalize_authoritative_definitions(value: object) -> list[Any] | None:
    return _normalize_object_list(value)


def _normalize_logical_type_options(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result = dict(value)
    required = result.get("required")
    if isinstance(required, list):
        result["required"] = sorted(item for item in required if isinstance(item, str))
    return result


def _normalize_description_object(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for key in ("usage", "purpose", "limitations"):
        if key in value:
            result[key] = value[key]
    if "authoritativeDefinitions" in value:
        normalized = _normalize_authoritative_definitions(value["authoritativeDefinitions"])
        if normalized is not None:
            result["authoritativeDefinitions"] = normalized
    if "customProperties" in value:
        normalized_cp = _normalize_custom_properties(value["customProperties"])
        if normalized_cp is not None:
            result["customProperties"] = normalized_cp
    return result


def _normalize_team_member(member: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(member)
    if "tags" in result:
        tags = _normalize_tags(result["tags"])
        if tags is not None:
            result["tags"] = tags
    if "authoritativeDefinitions" in result:
        auth = _normalize_authoritative_definitions(result["authoritativeDefinitions"])
        if auth is not None:
            result["authoritativeDefinitions"] = auth
    if "customProperties" in result:
        cps = _normalize_custom_properties(result["customProperties"])
        if cps is not None:
            result["customProperties"] = cps
    return result


def _normalize_team(value: object) -> dict[str, Any] | None:
    if isinstance(value, list):
        members = [_normalize_team_member(m) for m in value if isinstance(m, Mapping)]
        return {"members": _sort_by_canonical(members)}
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for key in ("id", "name", "description"):
        if key in value:
            result[key] = value[key]
    if "members" in value and isinstance(value["members"], list):
        members = [_normalize_team_member(m) for m in value["members"] if isinstance(m, Mapping)]
        result["members"] = _sort_by_canonical(members)
    if "tags" in value:
        tags = _normalize_tags(value["tags"])
        if tags is not None:
            result["tags"] = tags
    if "authoritativeDefinitions" in value:
        auth = _normalize_authoritative_definitions(value["authoritativeDefinitions"])
        if auth is not None:
            result["authoritativeDefinitions"] = auth
    if "customProperties" in value:
        cps = _normalize_custom_properties(value["customProperties"])
        if cps is not None:
            result["customProperties"] = cps
    return result


def _normalize_role(role: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(role)
    if "customProperties" in result:
        cps = _normalize_custom_properties(result["customProperties"])
        if cps is not None:
            result["customProperties"] = cps
    return result


def _normalize_roles(value: object) -> list[Any] | None:
    if not isinstance(value, list):
        return None
    roles = [_normalize_role(role) for role in value if isinstance(role, Mapping)]
    return _sort_by_canonical(roles)


def _odcs_provenance(
    document: Mapping[str, Any], *, contract_id: str, contract_version: str
) -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type="odcs",
        source_ref=contract_id,
        source_version=contract_version,
        observation_mode="declared",
    )


def _contract_attributes(document: Mapping[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "api_version": document["apiVersion"],
        "contract_version": document["version"],
        "status": document["status"],
    }
    for src, dst in (
        ("domain", "domain"),
        ("dataProduct", "data_product"),
    ):
        if src in document:
            attrs[dst] = document[src]
    if "tenant" in document and isinstance(document["tenant"], str):
        attrs["tenant"] = document["tenant"].casefold()
    if "tags" in document:
        tags = _normalize_tags(document["tags"])
        if tags is not None:
            attrs["tags"] = tags
    if "description" in document:
        desc = _normalize_description_object(document["description"])
        if desc is not None:
            attrs["description"] = desc
    if "team" in document:
        team = _normalize_team(document["team"])
        if team is not None:
            attrs["team"] = team
    if "roles" in document:
        roles = _normalize_roles(document["roles"])
        if roles is not None:
            attrs["roles"] = roles
    if "authoritativeDefinitions" in document:
        auth = _normalize_authoritative_definitions(document["authoritativeDefinitions"])
        if auth is not None:
            attrs["authoritative_definitions"] = auth
    if "customProperties" in document:
        cps = _normalize_custom_properties(document["customProperties"])
        if cps is not None:
            attrs["custom_properties"] = cps
    return attrs


def _dataset_attributes(schema_obj: Mapping[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if "id" in schema_obj:
        attrs["odcs_id"] = schema_obj["id"]
    for src, dst in (
        ("logicalType", "logical_type"),
        ("physicalName", "physical_name"),
        ("physicalType", "physical_type"),
        ("businessName", "business_name"),
        ("dataGranularityDescription", "data_granularity_description"),
    ):
        if src in schema_obj:
            attrs[dst] = schema_obj[src]
    if "tags" in schema_obj:
        tags = _normalize_tags(schema_obj["tags"])
        if tags is not None:
            attrs["tags"] = tags
    if "authoritativeDefinitions" in schema_obj:
        auth = _normalize_authoritative_definitions(schema_obj["authoritativeDefinitions"])
        if auth is not None:
            attrs["authoritative_definitions"] = auth
    if "customProperties" in schema_obj:
        cps = _normalize_custom_properties(schema_obj["customProperties"])
        if cps is not None:
            attrs["custom_properties"] = cps
    return attrs


def _column_attributes(prop: Mapping[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if "id" in prop:
        attrs["odcs_id"] = prop["id"]
    for src, dst in (
        ("businessName", "business_name"),
        ("logicalType", "logical_type"),
        ("physicalName", "physical_name"),
        ("physicalType", "physical_type"),
        ("required", "required"),
        ("unique", "unique"),
        ("primaryKey", "primary_key"),
        ("primaryKeyPosition", "primary_key_position"),
        ("partitioned", "partitioned"),
        ("partitionKeyPosition", "partition_key_position"),
        ("classification", "classification"),
        ("encryptedName", "encrypted_name"),
        ("criticalDataElement", "critical_data_element"),
    ):
        if src in prop:
            attrs[dst] = prop[src]
    if "logicalTypeOptions" in prop:
        opts = _normalize_logical_type_options(prop["logicalTypeOptions"])
        if opts is not None:
            attrs["logical_type_options"] = opts
    if "tags" in prop:
        tags = _normalize_tags(prop["tags"])
        if tags is not None:
            attrs["tags"] = tags
    if "authoritativeDefinitions" in prop:
        auth = _normalize_authoritative_definitions(prop["authoritativeDefinitions"])
        if auth is not None:
            attrs["authoritative_definitions"] = auth
    if "customProperties" in prop:
        cps = _normalize_custom_properties(prop["customProperties"])
        if cps is not None:
            attrs["custom_properties"] = cps
    return attrs


def _map_items_properties(
    items: object,
    *,
    namespace: str,
    parent_identity: GraphNodeIdentity,
    provenance: ProvenanceRecord,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    items_path: str,
) -> None:
    """Recurse through nested ``items`` wrappers; attach named properties to parent column."""
    current = items
    path = items_path
    while isinstance(current, Mapping):
        props = current.get("properties")
        if isinstance(props, list):
            _map_properties(
                props,
                namespace=namespace,
                parent_identity=parent_identity,
                provenance=provenance,
                nodes=nodes,
                edges=edges,
                base_path=f"{path}/properties",
            )
        current = current.get("items")
        path = f"{path}/items"


def _map_properties(
    properties: object,
    *,
    namespace: str,
    parent_identity: GraphNodeIdentity,
    provenance: ProvenanceRecord,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    base_path: str,
) -> None:
    if not isinstance(properties, list):
        return
    for index, prop in enumerate(properties):
        if not isinstance(prop, Mapping):
            continue
        prop_path = f"{base_path}/{index}"
        name = _require_mapped_str(
            prop.get("name"),
            path=f"{prop_path}/name",
            message="property name is required for graph mapping",
        )
        logical_id = _logical_identity(prop)
        if not logical_id:
            raise _mapping_error(
                prop_path,
                "property logical identity is required for graph mapping",
            )
        column_identity = GraphNodeIdentity(
            namespace,
            NODE_KIND_COLUMN,
            logical_id,
            parent=parent_identity,
        )
        description = prop.get("description")
        if description is not None and not isinstance(description, str):
            description = None
        nodes.append(
            GraphNode(
                identity=column_identity,
                name=name,
                description=description,
                attributes=_column_attributes(prop),
                provenance=(provenance,),
            )
        )
        edges.append(
            GraphEdge(
                source=parent_identity,
                target=column_identity,
                kind=EDGE_KIND_CONTAINS,
                provenance=(provenance,),
            )
        )
        _map_properties(
            prop.get("properties"),
            namespace=namespace,
            parent_identity=column_identity,
            provenance=provenance,
            nodes=nodes,
            edges=edges,
            base_path=f"{prop_path}/properties",
        )
        _map_items_properties(
            prop.get("items"),
            namespace=namespace,
            parent_identity=column_identity,
            provenance=provenance,
            nodes=nodes,
            edges=edges,
            items_path=f"{prop_path}/items",
        )


def _build_graph(document: Mapping[str, Any], *, namespace: str) -> GovernanceGraph:
    try:
        contract_id = _require_mapped_str(
            document.get("id"),
            path="/id",
            message="contract id is required for graph mapping",
        )
        contract_version = _require_mapped_str(
            document.get("version"),
            path="/version",
            message="contract version is required for graph mapping",
        )
        provenance = _odcs_provenance(
            document,
            contract_id=contract_id,
            contract_version=contract_version,
        )
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []

        contract_name = document.get("name")
        if not isinstance(contract_name, str) or not contract_name.strip():
            contract_name = contract_id
        else:
            contract_name = contract_name.strip()

        purpose: str | None = None
        description_obj = document.get("description")
        if isinstance(description_obj, Mapping):
            purpose_val = description_obj.get("purpose")
            if isinstance(purpose_val, str):
                purpose = purpose_val

        contract_identity = GraphNodeIdentity(namespace, NODE_KIND_CONTRACT, contract_id)
        nodes.append(
            GraphNode(
                identity=contract_identity,
                name=contract_name,
                description=purpose,
                attributes=_contract_attributes(document),
                provenance=(provenance,),
            )
        )

        schema_entries = document.get("schema")
        if isinstance(schema_entries, list):
            for index, schema_obj in enumerate(schema_entries):
                if not isinstance(schema_obj, Mapping):
                    continue
                schema_path = f"/schema/{index}"
                schema_name = _require_mapped_str(
                    schema_obj.get("name"),
                    path=f"{schema_path}/name",
                    message="schema object name is required for graph mapping",
                )
                dataset_logical_id = _logical_identity(schema_obj)
                if not dataset_logical_id:
                    raise _mapping_error(
                        schema_path,
                        "schema object logical identity is required for graph mapping",
                    )
                dataset_identity = GraphNodeIdentity(
                    namespace,
                    NODE_KIND_DATASET,
                    dataset_logical_id,
                )
                dataset_description = schema_obj.get("description")
                if dataset_description is not None and not isinstance(dataset_description, str):
                    dataset_description = None
                nodes.append(
                    GraphNode(
                        identity=dataset_identity,
                        name=schema_name,
                        description=dataset_description,
                        attributes=_dataset_attributes(schema_obj),
                        provenance=(provenance,),
                    )
                )
                edges.append(
                    GraphEdge(
                        source=contract_identity,
                        target=dataset_identity,
                        kind=EDGE_KIND_GOVERNS,
                        provenance=(provenance,),
                    )
                )
                _map_properties(
                    schema_obj.get("properties"),
                    namespace=namespace,
                    parent_identity=dataset_identity,
                    provenance=provenance,
                    nodes=nodes,
                    edges=edges,
                    base_path=f"{schema_path}/properties",
                )

        return GovernanceGraph.from_parts(nodes, edges)
    except OdcsMappingError:
        raise
    except (TypeError, ValueError) as exc:
        raise OdcsMappingError(
            [
                OdcsDiagnostic(
                    code=CODE_MAPPING,
                    path="",
                    message="failed to construct governance graph from ODCS document",
                )
            ]
        ) from exc
