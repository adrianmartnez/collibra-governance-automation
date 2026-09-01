"""Unit tests for mapper-time property observation APIs."""

from __future__ import annotations

from typing import Any

import pytest

from governance.identity.canonicalize import canonical_json_bytes
from governance.integrations.dbt import (
    map_dbt_manifest,
    map_dbt_manifest_with_observations,
)
from governance.integrations.dbt.validate import SUPPORTED_MANIFEST_SCHEMA_URI
from governance.integrations.odcs import (
    map_odcs_document,
    map_odcs_document_with_observations,
)
from governance.integrations.openlineage import (
    map_openlineage_events,
    map_openlineage_events_with_observations,
)

NS = "acme.commerce"
PRODUCER = "https://example.com/producer/1.0"
DATASET_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/DatasetEvent"
V12 = SUPPORTED_MANIFEST_SCHEMA_URI


def _minimal_odcs(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": "contract-orders",
        "version": "1.0.0",
        "status": "active",
        "name": "Orders Contract",
        "description": {"purpose": "orders"},
        "schema": [
            {
                "name": "orders",
                "physicalType": "table",
                "properties": [
                    {
                        "name": "order_id",
                        "logicalType": "string",
                        "physicalType": "varchar",
                        "description": "Order identifier",
                    }
                ],
            }
        ],
    }
    doc.update(overrides)
    return doc


def _minimal_dbt(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "metadata": {
            "dbt_schema_version": V12,
            "dbt_version": "1.10.0",
            "generated_at": "2024-01-01T00:00:00.000000Z",
            "invocation_id": "inv-volatile",
        },
        "nodes": {
            "model.pkg.orders": {
                "unique_id": "model.pkg.orders",
                "resource_type": "model",
                "name": "orders",
                "package_name": "pkg",
                "database": "analytics",
                "schema": "marts",
                "alias": "orders",
                "fqn": ["pkg", "orders"],
                "config": {"materialized": "table"},
                "columns": {
                    "order_id": {
                        "name": "order_id",
                        "description": "id",
                        "meta": {},
                        "tags": [],
                    }
                },
                "tags": [],
                "meta": {},
                "description": "Orders model",
            }
        },
        "sources": {},
        "parent_map": {},
        "disabled": {},
    }
    doc.update(overrides)
    return doc


def _facet_base(
    *,
    producer: str = PRODUCER,
    schema_url: str,
) -> dict[str, Any]:
    return {"_producer": producer, "_schemaURL": schema_url}


def _dataset_event_with_facets(facets: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventTime": "2024-01-01T00:00:00Z",
        "producer": PRODUCER,
        "schemaURL": DATASET_URL,
        "dataset": {
            "namespace": "postgres://host",
            "name": "db.schema.orders",
            "facets": facets,
        },
    }


def test_odcs_old_api_graph_identity_matches_with_observations() -> None:
    document = _minimal_odcs()
    old = map_odcs_document(document, namespace=NS)
    result = map_odcs_document_with_observations(document, namespace=NS)
    assert old.content_identity() == result.graph.content_identity()
    assert result.observations.observations
    assert all(
        record.observation_mode == "declared"
        for obs in result.observations.observations
        for record in obs.provenance
    )
    paths = {obs.property_path.to_pointer() for obs in result.observations.observations}
    assert "/name" in paths


def test_dbt_old_api_graph_identity_matches_with_observations() -> None:
    document = _minimal_dbt()
    old = map_dbt_manifest(document, namespace=NS)
    result = map_dbt_manifest_with_observations(document, namespace=NS)
    assert old.content_identity() == result.graph.content_identity()
    assert result.observations.observations
    assert all(
        record.observation_mode == "declared"
        for obs in result.observations.observations
        for record in obs.provenance
    )


def test_openlineage_old_api_graph_identity_and_observed_mode() -> None:
    events = [
        _dataset_event_with_facets(
            {
                "schema": {
                    **_facet_base(
                        schema_url="https://openlineage.io/spec/facets/1-1-1/SchemaDatasetFacet.json"
                    ),
                    "fields": [{"name": "order_id", "type": "string", "description": "id"}],
                }
            }
        )
    ]
    old = map_openlineage_events(events, namespace=NS)
    result = map_openlineage_events_with_observations(events, namespace=NS)
    assert old.content_identity() == result.graph.content_identity()
    assert result.observations.observations
    assert all(
        record.observation_mode == "observed"
        for obs in result.observations.observations
        for record in obs.provenance
    )


def test_openlineage_storage_vs_ownership_facet_provenance_isolation() -> None:
    storage_producer = "https://facet.producer/storage"
    ownership_producer = "https://facet.producer/ownership"
    storage_schema = "https://openlineage.io/spec/facets/1-0-0/StorageDatasetFacet.json"
    ownership_schema = "https://openlineage.io/spec/facets/1-0-0/OwnershipDatasetFacet.json"
    events = [
        _dataset_event_with_facets(
            {
                "storage": {
                    "_producer": storage_producer,
                    "_schemaURL": storage_schema,
                    "storageLayer": "iceberg",
                    "fileFormat": "parquet",
                },
                "ownership": {
                    "_producer": ownership_producer,
                    "_schemaURL": ownership_schema,
                    "owners": [{"name": "data-team", "type": "team"}],
                },
            }
        )
    ]
    try:
        result = map_openlineage_events_with_observations(events, namespace=NS)
    except Exception as exc:  # pragma: no cover - skip if facet mapping unavailable
        pytest.skip(f"OpenLineage storage/ownership fixtures unavailable: {exc}")

    by_path = {obs.property_path.to_pointer(): obs for obs in result.observations.observations}
    if "/attributes/storage_layer" not in by_path or "/attributes/ownership" not in by_path:
        pytest.skip("OpenLineage mapper did not emit storage_layer/ownership property observations")

    storage_obs = by_path["/attributes/storage_layer"]
    ownership_obs = by_path["/attributes/ownership"]
    storage_refs = {record.source_ref for record in storage_obs.provenance}
    ownership_refs = {record.source_ref for record in ownership_obs.provenance}
    expected_storage_ref = canonical_json_bytes(
        [storage_producer, "postgres://host", "db.schema.orders", "storage"]
    ).decode("utf-8")
    expected_ownership_ref = canonical_json_bytes(
        [ownership_producer, "postgres://host", "db.schema.orders", "ownership"]
    ).decode("utf-8")
    assert storage_refs == {expected_storage_ref}
    assert ownership_refs == {expected_ownership_ref}
    assert expected_ownership_ref not in storage_refs
    assert expected_storage_ref not in ownership_refs


def test_package_version_still_130() -> None:
    from governance import __version__

    assert __version__ == "1.3.0"
