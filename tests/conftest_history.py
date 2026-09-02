"""Pytest helpers for governance-history tests."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from conftest_comparison import build_sample_model, build_snapshot
from governance.domain.graph import GraphNodeIdentity, ProvenanceRecord
from governance.domain.observations import PropertyObservation, PropertyObservationSet, PropertyPath
from governance.history.models import (
    ComparisonPolicy,
    GovernanceHistory,
    HistoryEntry,
    HistoryEntryState,
    HistoryOperator,
)
from governance.history.serialize import write_history_artifact
from governance.observations.artifact import write_property_observation_set
from governance.snapshots import GovernanceSnapshot, write_snapshot

NS = "acme.commerce"
TABLE_PATH = ("sales", "orders")
TABLE_OBJECT = {"kind": "table", "path": list(TABLE_PATH)}
GOV_TABLE = {
    "namespace": NS,
    "kind": "table",
    "logical_id": "orders",
    "parent": None,
}


def write_sample_snapshot(
    path: Path,
    *,
    description: str | None = None,
    source_name: str = "governance-demo",
    database_name: str = "governance_demo",
    system_type: str = "postgresql",
    scanner: str | None = None,
) -> GovernanceSnapshot:
    snapshot = build_snapshot(
        description=description,
        source_name=source_name,
        database_name=database_name,
        system_type=system_type,
    )
    if scanner is not None:
        snapshot = GovernanceSnapshot(
            model=snapshot.model,
            source_name=snapshot.source_name,
            database_name=snapshot.database_name,
            system_type=snapshot.system_type,
            scanner=scanner,
            scanner_contract_version=snapshot.scanner_contract_version,
        )
    write_snapshot(snapshot, path)
    return snapshot


def build_observation_set(
    *,
    value: object = "orders table",
    provider: str = "odcs",
    source_ref: str = "customer-contract",
    logical_id: str = "orders",
    property_pointer: str = "/description",
) -> PropertyObservationSet:
    identity = GraphNodeIdentity(NS, "table", logical_id)
    path = PropertyPath.parse(property_pointer)
    obs = PropertyObservation(
        object_identity=identity,
        property_path=path,
        value=value,
        provenance=(
            ProvenanceRecord(
                provider_type=provider,
                source_ref=source_ref,
                source_version="1.0",
                observation_mode="observed",
            ),
        ),
    )
    return PropertyObservationSet.from_observations([obs])


def write_observations(path: Path, observation_set: PropertyObservationSet | None = None) -> Path:
    payload = observation_set if observation_set is not None else build_observation_set()
    write_property_observation_set(payload, path)
    return path


def write_authority_yaml(
    path: Path,
    *,
    rule_id: str = "table-description-odcs",
    kind: str = "table",
    property_pointer: str = "/description",
    provider_type: str = "odcs",
    source_ref: str | None = "customer-contract",
    namespace: str | None = None,
) -> Path:
    authority: dict = {"provider_type": provider_type}
    if source_ref is not None:
        authority["source_ref"] = source_ref
    select: dict = {"kind": kind, "property": property_pointer}
    if namespace is not None:
        select["namespace"] = namespace
    document = {
        "authority_schema": "governance-authority",
        "authority_version": "1",
        "rules": [
            {
                "id": rule_id,
                "select": select,
                "authority": authority,
            }
        ],
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def write_history_direct(
    path: Path,
    *,
    entries: list[HistoryEntry],
    align_source_roots: bool = False,
    align_database_roots: bool = False,
) -> GovernanceHistory:
    history = GovernanceHistory(
        comparison_policy=ComparisonPolicy(
            align_source_roots=align_source_roots,
            align_database_roots=align_database_roots,
        ),
        entries=tuple(entries),
    )
    write_history_artifact(history, path)
    return history


def history_entry_for_snapshot(
    snapshot: GovernanceSnapshot,
    snapshot_rel: str,
    *,
    observations_rel: str | None = None,
    authority_rels: tuple[str, ...] | None = None,
    labels: dict[str, str] | None = None,
    context: dict | None = None,
    captured_at: str | None = None,
) -> HistoryEntry:
    state = HistoryEntryState(
        snapshot=snapshot.content_identity(),
        labels=labels,
        context=context,
    )
    operator = HistoryOperator(
        snapshot_path=snapshot_rel,
        observations_path=observations_rel,
        authority_paths=authority_rels,
    )
    return HistoryEntry(state=state, operator=operator, captured_at=captured_at)


def object_json(**kwargs) -> str:
    payload = dict(TABLE_OBJECT)
    payload.update(kwargs)
    return json.dumps(payload, separators=(",", ":"))


def governance_object_json(**kwargs) -> str:
    payload = dict(GOV_TABLE)
    payload.update(kwargs)
    return json.dumps(payload, separators=(",", ":"))


__all__ = [
    "GOV_TABLE",
    "NS",
    "TABLE_OBJECT",
    "TABLE_PATH",
    "build_observation_set",
    "build_sample_model",
    "build_snapshot",
    "governance_object_json",
    "history_entry_for_snapshot",
    "object_json",
    "write_authority_yaml",
    "write_history_direct",
    "write_observations",
    "write_sample_snapshot",
]
