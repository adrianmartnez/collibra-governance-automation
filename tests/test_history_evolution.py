"""History evolution / show contract tests."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from conftest_history import (
    build_observation_set,
    object_json,
    write_authority_yaml,
    write_observations,
    write_sample_snapshot,
)
from governance.cli import main
from governance.comparison.projection import ComparisonObjectIdentity
from governance.domain.graph import GraphNodeIdentity, ProvenanceRecord
from governance.domain.observations import (
    PropertyObservation,
    PropertyObservationSet,
    PropertyPath,
)
from governance.history import (
    HistoryError,
    append_history_entry,
    build_history_evolution,
    load_history_artifact,
    resolve_history_artifacts,
)

_EVOLUTION_VALIDATOR: Draft202012Validator | None = None


def _evolution_validator() -> Draft202012Validator:
    global _EVOLUTION_VALIDATOR
    if _EVOLUTION_VALIDATOR is None:
        text = (
            files("governance.history.schemas")
            .joinpath("governance-history-evolution.v1.schema.json")
            .read_text(encoding="utf-8")
        )
        _EVOLUTION_VALIDATOR = Draft202012Validator(json.loads(text))
    return _EVOLUTION_VALIDATOR


def assert_valid_evolution(result: dict) -> None:
    _evolution_validator().validate(result)


def _dual_obs(
    *,
    alpha_provider: str = "odcs",
    alpha_ref: str = "c1",
    beta_provider: str = "dbt",
    beta_ref: str = "model.orders",
    alpha_value: object = "alpha",
    beta_value: object = "beta",
) -> PropertyObservationSet:
    identity = GraphNodeIdentity("acme.commerce", "table", "orders")
    path = PropertyPath.parse("/description")
    return PropertyObservationSet.from_observations(
        [
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value=alpha_value,
                provenance=(ProvenanceRecord(alpha_provider, alpha_ref, "1.0", "observed"),),
            ),
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value=beta_value,
                provenance=(ProvenanceRecord(beta_provider, beta_ref, "1.0", "observed"),),
            ),
        ]
    )


def _agreeing_obs() -> PropertyObservationSet:
    identity = GraphNodeIdentity("acme.commerce", "table", "orders")
    path = PropertyPath.parse("/description")
    return PropertyObservationSet.from_observations(
        [
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="same",
                provenance=(
                    ProvenanceRecord("odcs", "c1", "1.0", "observed"),
                    ProvenanceRecord("dbt", "model.orders", "1.0", "observed"),
                ),
            ),
        ]
    )


def _add_pair(tmp_path: Path, *, description_b: str | None = "updated") -> Path:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description=description_b)
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    append_history_entry(history, snapshot_path="b.json")
    return history


def test_snapshot_property_unchanged_explicit_emit(tmp_path: Path) -> None:
    # Same snapshot property values; labels make adjacent entry states distinct.
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description=None)
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json", labels={"n": "1"})
    append_history_entry(history, snapshot_path="b.json", labels={"n": "2"})
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_object=ComparisonObjectIdentity(kind="table", path=("sales", "orders")),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    prop = result["transitions"][0]["snapshot"]["property"]
    assert prop is not None
    assert prop["property"] == "/description"
    assert prop["baseline"] == prop["candidate"]


def test_object_changed_and_context_only_null_snapshot(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="updated")
    write_observations(tmp_path / "obs_a.json")
    write_observations(tmp_path / "obs_b.json", build_observation_set(value="updated"))
    write_authority_yaml(tmp_path / "auth.yaml")
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs_a.json",
        authority_paths=["auth.yaml"],
    )
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)

    with_object = build_history_evolution(
        resolved,
        queried_object=ComparisonObjectIdentity(kind="table", path=("sales", "orders")),
    )
    assert_valid_evolution(with_object)
    assert with_object["transitions"][0]["snapshot"]["object_change"] is not None

    context_only = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
    )
    assert_valid_evolution(context_only)
    assert context_only["transitions"][0]["snapshot"]["object_change"] is None
    assert context_only["transitions"][0]["snapshot"]["property"] is None


def test_provenance_only_change_not_conflict_change(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(
        tmp_path / "obs_a.json",
        build_observation_set(value="same", source_ref="contract-a"),
    )
    write_observations(
        tmp_path / "obs_b.json",
        build_observation_set(value="same", source_ref="contract-b"),
    )
    write_authority_yaml(tmp_path / "auth.yaml", source_ref=None)
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs_a.json",
        authority_paths=["auth.yaml"],
    )
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    ctx = result["transitions"][0]["context_property_changes"][0]
    assert ctx["provenance"]["change"] is not None
    assert ctx["conflict"]["change"] is None
    # Provenance-only availability (no authority on both sides would differ);
    # here full context: conflict available but change null (orthogonality).
    assert ctx["authority_decision"]["change"] is None


def test_conflict_state_change_unresolved_to_resolved(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_a.json", _dual_obs())
    write_observations(tmp_path / "obs_b.json", _dual_obs())
    write_authority_yaml(
        tmp_path / "auth_none.yaml",
        rule_id="name-only",
        property_pointer="/name",
        provider_type="odcs",
        source_ref=None,
    )
    write_authority_yaml(tmp_path / "auth_match.yaml", source_ref="c1")
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs_a.json",
        authority_paths=["auth_none.yaml"],
    )
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth_match.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    conflict = result["transitions"][0]["context_property_changes"][0]["conflict"]
    assert conflict["change"] is not None
    assert conflict["change"]["baseline"]["state"] == "UNRESOLVED_CONFLICT"
    assert conflict["change"]["candidate"]["state"] == "RESOLVED_BY_AUTHORITY"


def test_single_observation_to_agreement_authority_decision_null(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_a.json", build_observation_set(value="same"))
    write_observations(tmp_path / "obs_b.json", _agreeing_obs())
    write_authority_yaml(tmp_path / "auth.yaml", source_ref="c1")
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs_a.json",
        authority_paths=["auth.yaml"],
    )
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    auth = result["transitions"][0]["context_property_changes"][0]["authority_decision"]
    assert auth["change"] is None
    assert auth["available"] == {"baseline": True, "candidate": True}


def test_unrelated_authority_rule_authority_decision_null(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_a.json", _dual_obs())
    write_observations(tmp_path / "obs_b.json", _dual_obs(alpha_value="alpha2", beta_value="beta2"))
    write_authority_yaml(
        tmp_path / "auth.yaml",
        rule_id="name-only",
        property_pointer="/name",
        provider_type="odcs",
        source_ref=None,
    )
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs_a.json",
        authority_paths=["auth.yaml"],
    )
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    ctx = result["transitions"][0]["context_property_changes"][0]
    assert ctx["authority_decision"]["change"] is None
    assert ctx["conflict"]["change"] is None  # both NO_AUTHORITY_RULE


def test_no_authority_rule_to_matching_rule_authority_change(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_a.json", _dual_obs())
    write_observations(tmp_path / "obs_b.json", _dual_obs())
    write_authority_yaml(
        tmp_path / "auth_none.yaml",
        rule_id="name-only",
        property_pointer="/name",
        provider_type="odcs",
        source_ref=None,
    )
    write_authority_yaml(tmp_path / "auth_match.yaml", source_ref="c1")
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs_a.json",
        authority_paths=["auth_none.yaml"],
    )
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth_match.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    auth = result["transitions"][0]["context_property_changes"][0]["authority_decision"]
    assert auth["change"] is not None
    assert auth["change"]["baseline"]["authority_applicable"] is True
    assert auth["change"]["baseline"]["outcome"] == "NO_AUTHORITY_RULE"
    assert auth["change"]["candidate"]["outcome"] == "RESOLVED_BY_AUTHORITY"


def test_winning_rule_key_change_authority_non_null(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_a.json", _dual_obs())
    write_observations(tmp_path / "obs_b.json", _dual_obs())
    write_authority_yaml(tmp_path / "auth_odcs.yaml", rule_id="odcs-rule", source_ref="c1")
    write_authority_yaml(
        tmp_path / "auth_dbt.yaml",
        rule_id="dbt-rule",
        provider_type="dbt",
        source_ref="model.orders",
    )
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs_a.json",
        authority_paths=["auth_odcs.yaml"],
    )
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth_dbt.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    auth = result["transitions"][0]["context_property_changes"][0]["authority_decision"]
    assert auth["change"] is not None
    assert (
        auth["change"]["baseline"]["winning_rule_key"]
        != auth["change"]["candidate"]["winning_rule_key"]
    )


def test_resolved_to_authoritative_source_conflicted(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_a.json", _dual_obs())
    # Candidate: two distinct values both from odcs/c1 -> AUTHORITATIVE_SOURCE_CONFLICTED
    identity = GraphNodeIdentity("acme.commerce", "table", "orders")
    path = PropertyPath.parse("/description")
    conflicted = PropertyObservationSet.from_observations(
        [
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="alpha",
                provenance=(ProvenanceRecord("odcs", "c1", "1.0", "observed"),),
            ),
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="gamma",
                provenance=(ProvenanceRecord("odcs", "c1", "2.0", "observed"),),
            ),
        ]
    )
    write_observations(tmp_path / "obs_b.json", conflicted)
    write_authority_yaml(tmp_path / "auth.yaml", source_ref="c1")
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs_a.json",
        authority_paths=["auth.yaml"],
    )
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=identity,
        queried_property="/description",
    )
    assert_valid_evolution(result)
    auth = result["transitions"][0]["context_property_changes"][0]["authority_decision"]
    assert auth["change"] is not None
    assert auth["change"]["baseline"]["outcome"] == "RESOLVED_BY_AUTHORITY"
    assert auth["change"]["candidate"]["outcome"] == "AUTHORITATIVE_SOURCE_CONFLICTED"


def test_effective_value_transitions(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_sample_snapshot(tmp_path / "c.json", description="y")
    write_observations(tmp_path / "obs_a.json", _dual_obs(alpha_value="A", beta_value="Z"))
    write_observations(tmp_path / "obs_b.json", _dual_obs(alpha_value="B", beta_value="Z"))
    # No effective: authoritative source not observed
    write_observations(
        tmp_path / "obs_c.json",
        _dual_obs(
            alpha_provider="other",
            alpha_ref="x",
            alpha_value="C",
            beta_value="Z",
        ),
    )
    write_authority_yaml(tmp_path / "auth.yaml", source_ref="c1")
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs_a.json",
        authority_paths=["auth.yaml"],
    )
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth.yaml"],
    )
    append_history_entry(
        history,
        snapshot_path="c.json",
        observations_path="obs_c.json",
        authority_paths=["auth.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    t0 = result["transitions"][0]["context_property_changes"][0]["effective_value"]
    assert t0["change"] is not None
    assert t0["change"]["baseline"]["value"] == "A"
    assert t0["change"]["candidate"]["value"] == "B"
    t1 = result["transitions"][1]["context_property_changes"][0]["effective_value"]
    assert t1["change"] is not None
    assert t1["change"]["baseline"]["has_effective_value"] is True
    assert t1["change"]["candidate"]["has_effective_value"] is False


def test_context_unavailable_not_effective_removal(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_a.json", build_observation_set(value="A"))
    write_authority_yaml(tmp_path / "auth.yaml", source_ref="customer-contract")
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs_a.json",
        authority_paths=["auth.yaml"],
    )
    append_history_entry(history, snapshot_path="b.json")  # snapshot-only: context unavailable
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    eff = result["transitions"][0]["context_property_changes"][0]["effective_value"]
    assert eff["available"] == {"baseline": True, "candidate": False}
    assert eff["change"] is None  # unavailable != present->no-effective removal


def test_added_object_explicit_property_missing_to_present(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    # Candidate adds a new table via different model — use description change on known table
    # and query a property that appears only later via extra column description path.
    from conftest_comparison import build_sample_model
    from governance.domain import Column, make_column_id
    from governance.snapshots import GovernanceSnapshot, write_snapshot

    model_a = build_sample_model(description=None, include_fk=False, extra_column=None)
    write_snapshot(GovernanceSnapshot.from_model(model_a), tmp_path / "a2.json")
    extra = Column(
        id=make_column_id("governance-demo", "governance_demo", "sales", "orders", "amount"),
        name="amount",
        data_type="numeric",
        ordinal_position=2,
        nullable=True,
        description="amount col",
    )
    model_b = build_sample_model(description=None, include_fk=False, extra_column=extra)
    write_snapshot(GovernanceSnapshot.from_model(model_b), tmp_path / "b2.json")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a2.json")
    append_history_entry(history, snapshot_path="b2.json")
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_object=ComparisonObjectIdentity(kind="column", path=("sales", "orders", "amount")),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    prop = result["transitions"][0]["snapshot"]["property"]
    assert prop is not None
    assert prop["baseline"] == {"has_value": False}
    assert prop["candidate"]["has_value"] is True


def test_removed_present_to_missing(tmp_path: Path) -> None:
    from conftest_comparison import build_sample_model
    from governance.domain import Column, make_column_id
    from governance.snapshots import GovernanceSnapshot, write_snapshot

    extra = Column(
        id=make_column_id("governance-demo", "governance_demo", "sales", "orders", "amount"),
        name="amount",
        data_type="numeric",
        ordinal_position=2,
        nullable=True,
        description="amount col",
    )
    write_snapshot(
        GovernanceSnapshot.from_model(build_sample_model(include_fk=False, extra_column=extra)),
        tmp_path / "a.json",
    )
    write_snapshot(
        GovernanceSnapshot.from_model(build_sample_model(include_fk=False)),
        tmp_path / "b.json",
    )
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json")
    append_history_entry(history, snapshot_path="b.json")
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_object=ComparisonObjectIdentity(kind="column", path=("sales", "orders", "amount")),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    prop = result["transitions"][0]["snapshot"]["property"]
    assert prop is not None
    assert prop["baseline"]["has_value"] is True
    assert prop["candidate"] == {"has_value": False}


def test_technical_attr_missing_vs_null(tmp_path: Path) -> None:
    from conftest_comparison import build_sample_model
    from governance.snapshots import GovernanceSnapshot, write_snapshot

    write_snapshot(
        GovernanceSnapshot.from_model(build_sample_model(technical_attributes={})),
        tmp_path / "a.json",
    )
    write_snapshot(
        GovernanceSnapshot.from_model(build_sample_model(technical_attributes={"flag": None})),
        tmp_path / "b.json",
    )
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json", labels={"n": "1"})
    append_history_entry(history, snapshot_path="b.json", labels={"n": "2"})
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_object=ComparisonObjectIdentity(kind="column", path=("sales", "orders", "id")),
        queried_property="/technical_attributes/flag",
    )
    assert_valid_evolution(result)
    prop = result["transitions"][0]["snapshot"]["property"]
    assert prop is not None
    # Missing key vs explicit null are distinct comparable states when projected.
    assert prop["baseline"] != prop["candidate"]


def test_object_level_context_union_deterministic(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    identity = GraphNodeIdentity("acme.commerce", "table", "orders")
    obs_a = PropertyObservationSet.from_observations(
        [
            PropertyObservation(
                object_identity=identity,
                property_path=PropertyPath.parse("/description"),
                value="a",
                provenance=(ProvenanceRecord("odcs", "c1", "1.0", "observed"),),
            ),
            PropertyObservation(
                object_identity=identity,
                property_path=PropertyPath.parse("/name"),
                value="orders",
                provenance=(ProvenanceRecord("odcs", "c1", "1.0", "observed"),),
            ),
        ]
    )
    obs_b = PropertyObservationSet.from_observations(
        [
            PropertyObservation(
                object_identity=identity,
                property_path=PropertyPath.parse("/name"),
                value="orders2",
                provenance=(ProvenanceRecord("odcs", "c1", "1.0", "observed"),),
            ),
            PropertyObservation(
                object_identity=identity,
                property_path=PropertyPath.parse("/description"),
                value="b",
                provenance=(ProvenanceRecord("odcs", "c1", "1.0", "observed"),),
            ),
        ]
    )
    write_observations(tmp_path / "obs_a.json", obs_a)
    write_observations(tmp_path / "obs_b.json", obs_b)
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json", observations_path="obs_a.json")
    append_history_entry(history, snapshot_path="b.json", observations_path="obs_b.json")
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(resolved, queried_governance_object=identity)
    assert_valid_evolution(result)
    props = [item["property"] for item in result["transitions"][0]["context_property_changes"]]
    assert props == sorted(props)
    assert props == ["/description", "/name"]


def test_explicit_unchanged_context_property_emitted(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_a.json", build_observation_set(value="same"))
    write_observations(tmp_path / "obs_b.json", build_observation_set(value="same"))
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json", observations_path="obs_a.json")
    append_history_entry(history, snapshot_path="b.json", observations_path="obs_b.json")
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    ctx = result["transitions"][0]["context_property_changes"]
    assert len(ctx) == 1
    assert ctx[0]["property"] == "/description"
    assert ctx[0]["provenance"]["change"] is None


def test_no_result_to_result_effective(tmp_path: Path) -> None:
    # Baseline provenance-only (no conflict results) -> candidate full context with result.
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="x")
    write_observations(tmp_path / "obs_a.json", build_observation_set(value="A"))
    write_observations(tmp_path / "obs_b.json", build_observation_set(value="B"))
    write_authority_yaml(tmp_path / "auth.yaml")
    history = tmp_path / "history.json"
    append_history_entry(history, snapshot_path="a.json", observations_path="obs_a.json")
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    eff = result["transitions"][0]["context_property_changes"][0]["effective_value"]
    assert eff["available"] == {"baseline": False, "candidate": True}
    assert eff["change"] is None  # availability gate, not a change block


def test_object_not_found(tmp_path: Path) -> None:
    history = _add_pair(tmp_path)
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    with pytest.raises(HistoryError) as exc_info:
        build_history_evolution(
            resolved,
            queried_object=ComparisonObjectIdentity(kind="table", path=("missing", "table")),
        )
    assert exc_info.value.errors[0].code == "object_not_found"


def test_show_cli_json(tmp_path: Path, capsys) -> None:
    history = _add_pair(tmp_path)
    code = main(
        [
            "history",
            "show",
            "--history",
            str(history),
            "--object",
            object_json(),
            "--property",
            "/description",
            "--format",
            "json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["evolution_schema"] == "governance-history-evolution"
    assert payload["writes_performed"] == 0
    assert len(payload["transitions"]) == 1
    assert_valid_evolution(payload)


def test_moved_path_mtime_captured_at_same_evolution_bytes(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="updated")
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        captured_at="2020-01-01T00:00:00Z",
    )
    append_history_entry(
        history,
        snapshot_path="b.json",
        captured_at="2020-01-02T00:00:00Z",
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    first = build_history_evolution(
        resolved,
        queried_object=ComparisonObjectIdentity(kind="table", path=("sales", "orders")),
        queried_property="/description",
    )
    # Move artifacts under nested dir with different relative operator paths.
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "a.json").replace(nested / "a.json")
    (tmp_path / "b.json").replace(nested / "b.json")
    history2 = nested / "history.json"
    append_history_entry(
        history2,
        snapshot_path="a.json",
        captured_at="2099-01-01T00:00:00.123456Z",
    )
    append_history_entry(
        history2,
        snapshot_path="b.json",
        captured_at="2099-06-01T12:00:00Z",
    )
    resolved2 = resolve_history_artifacts(load_history_artifact(history2), history2)
    second = build_history_evolution(
        resolved2,
        queried_object=ComparisonObjectIdentity(kind="table", path=("sales", "orders")),
        queried_property="/description",
    )
    assert_valid_evolution(first)
    assert_valid_evolution(second)
    assert first["content_identity"] == second["content_identity"]
    # Drop identity-stable fields that include history digests which may differ if
    # captured_at/operator excluded — history identity ignores operator/captured_at.
    assert first["transitions"] == second["transitions"]


def test_evolution_schema_rejects_garbage_state_and_reason() -> None:
    schema = _evolution_validator().schema
    conflict_validator = Draft202012Validator(
        {"$ref": "#/$defs/conflictStateProjection", "$defs": schema["$defs"]}
    )
    assert conflict_validator.is_valid({"has_result": False})
    assert conflict_validator.is_valid(
        {"has_result": True, "state": "AGREEMENT", "reason": "AGREEMENT"}
    )
    assert not conflict_validator.is_valid(
        {"has_result": True, "state": "GARBAGE", "reason": "AGREEMENT"}
    )
    assert not conflict_validator.is_valid(
        {"has_result": True, "state": "AGREEMENT", "reason": "NOT_A_REASON"}
    )


def test_evolution_schema_rejects_junk_winning_rule_key() -> None:
    schema = _evolution_validator().schema
    auth_validator = Draft202012Validator(
        {"$ref": "#/$defs/authorityDecisionProjection", "$defs": schema["$defs"]}
    )
    valid_key = {
        "authority": {"provider_type": "odcs", "source_ref": "c1"},
        "select": {"kind": "table", "property": "/description"},
    }
    assert auth_validator.is_valid(
        {
            "has_result": True,
            "authority_applicable": True,
            "outcome": "RESOLVED_BY_AUTHORITY",
            "winning_rule_key": valid_key,
        }
    )
    assert auth_validator.is_valid(
        {
            "has_result": True,
            "authority_applicable": True,
            "outcome": "NO_AUTHORITY_RULE",
            "winning_rule_key": None,
        }
    )
    assert not auth_validator.is_valid(
        {
            "has_result": True,
            "authority_applicable": True,
            "outcome": "RESOLVED_BY_AUTHORITY",
            "winning_rule_key": {"junk": True},
        }
    )
    assert not auth_validator.is_valid(
        {
            "has_result": True,
            "authority_applicable": True,
            "outcome": "RESOLVED_BY_AUTHORITY",
            "winning_rule_key": {
                "authority": {"provider_type": "odcs"},
                "select": {"kind": "table", "property": "/description", "extra": 1},
            },
        }
    )


def test_evolution_builder_output_accepted_by_tightened_schema(tmp_path: Path) -> None:
    write_sample_snapshot(tmp_path / "a.json")
    write_sample_snapshot(tmp_path / "b.json", description="updated")
    write_observations(tmp_path / "obs_a.json", _dual_obs())
    write_observations(tmp_path / "obs_b.json", _dual_obs())
    write_authority_yaml(tmp_path / "auth_odcs.yaml", rule_id="odcs-rule", source_ref="c1")
    write_authority_yaml(
        tmp_path / "auth_dbt.yaml",
        rule_id="dbt-rule",
        provider_type="dbt",
        source_ref="model.orders",
    )
    history = tmp_path / "history.json"
    append_history_entry(
        history,
        snapshot_path="a.json",
        observations_path="obs_a.json",
        authority_paths=["auth_odcs.yaml"],
    )
    append_history_entry(
        history,
        snapshot_path="b.json",
        observations_path="obs_b.json",
        authority_paths=["auth_dbt.yaml"],
    )
    resolved = resolve_history_artifacts(load_history_artifact(history), history)
    result = build_history_evolution(
        resolved,
        queried_governance_object=GraphNodeIdentity("acme.commerce", "table", "orders"),
        queried_property="/description",
    )
    assert_valid_evolution(result)
    auth = result["transitions"][0]["context_property_changes"][0]["authority_decision"]
    assert auth["change"] is not None
    winning = auth["change"]["baseline"]["winning_rule_key"]
    assert winning is not None
    assert list(winning.keys()) == ["authority", "select"]


def test_evolution_schema_iter_errors_recursion_mapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from governance.history.result import validate_evolution_result_against_schema

    class _BoomValidator:
        def iter_errors(self, _payload):
            raise RecursionError("too deep")

    monkeypatch.setattr(
        "governance.history.result._get_evolution_validator",
        lambda: _BoomValidator(),
    )
    with pytest.raises(HistoryError) as exc_info:
        validate_evolution_result_against_schema({"anything": True})
    assert "too deeply nested" in exc_info.value.errors[0].message
    assert exc_info.value.errors[0].code == "invalid_history_artifact"
