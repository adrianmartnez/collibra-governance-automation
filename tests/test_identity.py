"""Unit tests for versioned governance content identities."""

from __future__ import annotations

import json
from pathlib import Path

from governance.config_contract import load_canonical_config
from governance.domain import (
    Column,
    Database,
    DataSource,
    GovernanceModel,
    Schema,
    Table,
    make_column_id,
    make_database_id,
    make_datasource_id,
    make_schema_id,
    make_table_id,
)
from governance.identity import (
    HASHING_CONTRACT_VERSION,
    config_identity,
    graph_identity,
    mapping_identity,
    plan_identity,
    policy_identity,
    remote_state_identity,
    snapshot_identity,
    target_context_identity,
)
from governance.integrations.collibra import load_mapping_config_file, mock_mapping_config
from governance.snapshots import GovernanceSnapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def test_content_identity_shape() -> None:
    identity = config_identity({"schema_version": "1", "sources": []})
    assert identity.algorithm == "sha256"
    assert identity.hashing_contract_version == HASHING_CONTRACT_VERSION
    assert len(identity.digest) == 64
    assert identity.digest == identity.digest.lower()


def test_order_invariance_of_canonical_json() -> None:
    a = config_identity({"b": 1, "a": 2})
    b = config_identity({"a": 2, "b": 1})
    assert a == b


def test_domain_separation_between_components() -> None:
    payload = {"x": 1}
    identities = [
        config_identity(payload),
        snapshot_identity(payload),
        mapping_identity(payload),
        policy_identity(payload),
        plan_identity(payload),
        remote_state_identity(payload),
        target_context_identity(payload),
        graph_identity(payload),
    ]
    digests = [identity.digest for identity in identities]
    assert len(set(digests)) == len(digests)
    assert config_identity(payload) != graph_identity(payload)
    assert snapshot_identity(payload) != graph_identity(payload)


def test_mapping_identity_ignores_path(tmp_path: Path) -> None:
    src = FIXTURES / "mapping.json"
    a_path = tmp_path / "a" / "mapping.json"
    b_path = tmp_path / "b" / "mapping.json"
    a_path.parent.mkdir()
    b_path.parent.mkdir()
    a_path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    # Formatting-only change (whitespace) after load still yields same normalized identity.
    pretty = json.dumps(json.loads(src.read_text(encoding="utf-8")), indent=4)
    b_path.write_text(pretty, encoding="utf-8")

    cfg_a = load_mapping_config_file(a_path)
    cfg_b = load_mapping_config_file(b_path)
    assert mapping_identity(cfg_a.to_identity_dict()) == mapping_identity(cfg_b.to_identity_dict())

    mock = mock_mapping_config()
    assert mapping_identity(cfg_a.to_identity_dict()) == mapping_identity(mock.to_identity_dict())


def test_snapshot_identity_matches_persisted_field() -> None:
    source_name = "s"
    database_name = "d"
    schema_name = "sch"
    table_name = "t"
    column_name = "c"
    datasource_id = make_datasource_id(source_name)
    database_id = make_database_id(source_name, database_name)
    schema_id = make_schema_id(source_name, database_name, schema_name)
    table_id = make_table_id(source_name, database_name, schema_name, table_name)
    column_id = make_column_id(source_name, database_name, schema_name, table_name, column_name)
    model = GovernanceModel(
        data_sources=(
            DataSource(
                id=datasource_id,
                name=source_name,
                system_type="postgresql",
                databases=(
                    Database(
                        id=database_id,
                        name=database_name,
                        datasource_id=datasource_id,
                        schemas=(
                            Schema(
                                id=schema_id,
                                name=schema_name,
                                database_id=database_id,
                                tables=(
                                    Table(
                                        id=table_id,
                                        name=table_name,
                                        schema_id=schema_id,
                                        columns=(
                                            Column(
                                                id=column_id,
                                                name=column_name,
                                                data_type="integer",
                                                ordinal_position=1,
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    snapshot = GovernanceSnapshot.from_model(model)
    expected = snapshot_identity(snapshot.canonical_dict_without_identity())
    assert snapshot.content_identity() == expected


def test_config_identity_stable_across_checkouts(tmp_path: Path) -> None:
    text = (FIXTURES / "valid_full.yaml").read_text(encoding="utf-8")
    a = tmp_path / "checkout-a" / "governance.yaml"
    b = tmp_path / "checkout-b" / "governance.yaml"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text(text, encoding="utf-8")
    b.write_text(text, encoding="utf-8")
    id_a = config_identity(load_canonical_config(a).identity_projection())
    id_b = config_identity(load_canonical_config(b).identity_projection())
    assert id_a == id_b
