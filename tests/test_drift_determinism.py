"""Determinism tests for drift policy and result identities."""

from __future__ import annotations

from pathlib import Path

from conftest_comparison import build_snapshot
from conftest_drift import write_policy
from governance.comparison import (
    build_comparison_result,
    canonical_comparison_json,
    write_comparison_artifact,
)
from governance.drift import canonical_drift_json
from governance.drift.load import load_drift_policy
from governance.drift.result import build_drift_result
from governance.identity.hashing import drift_policy_identity, drift_result_identity


def _description_policy(rule_id: str) -> str:
    return f"""drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: {rule_id}
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
"""


def test_policy_identity_changes_on_rule_id_rename(tmp_path: Path) -> None:
    policy_a_path = tmp_path / "a.yaml"
    policy_b_path = tmp_path / "b.yaml"
    write_policy(policy_a_path, _description_policy("allow-description"))
    write_policy(policy_b_path, _description_policy("renamed-id"))
    policy_a = load_drift_policy(policy_a_path)
    policy_b = load_drift_policy(policy_b_path)
    identity_a = drift_policy_identity(policy_a.to_identity_dict()).digest
    identity_b = drift_policy_identity(policy_b.to_identity_dict()).digest
    assert identity_a != identity_b


def test_policy_identity_stable_for_same_semantics(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_policy(policy_path, _description_policy("allow-description"))
    first = load_drift_policy(policy_path)
    second = load_drift_policy(policy_path)
    assert (
        drift_policy_identity(first.to_identity_dict()).digest
        == drift_policy_identity(second.to_identity_dict()).digest
    )


def test_drift_result_content_identity_is_deterministic(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_policy(policy_path, _description_policy("allow-description"))
    policy = load_drift_policy(policy_path)
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
    )
    first = build_drift_result(comparison, policy)
    second = build_drift_result(comparison, policy)
    assert first["content_identity"] == second["content_identity"]
    assert canonical_drift_json(first) == canonical_drift_json(second)


def test_drift_result_identity_matches_recomputed_digest() -> None:
    comparison = build_comparison_result(build_snapshot(), build_snapshot())
    result = build_drift_result(comparison, None)
    without_identity = {key: value for key, value in result.items() if key != "content_identity"}
    expected = drift_result_identity(without_identity).to_dict()
    assert result["content_identity"] == expected


def test_comparison_canonical_json_is_stable_across_write(tmp_path: Path) -> None:
    comparison = build_comparison_result(build_snapshot(), build_snapshot())
    artifact = tmp_path / "cmp.json"
    write_comparison_artifact(comparison, artifact)
    text = artifact.read_text(encoding="utf-8")
    assert text == canonical_comparison_json(comparison)


def test_policy_identity_invariant_to_rule_reorder(tmp_path: Path) -> None:
    policy_a = tmp_path / "a.yaml"
    policy_b = tmp_path / "b.yaml"
    write_policy(
        policy_a,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: first
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
  - id: second
    match:
      change: changed
      object:
        kind: table
        path: ["sales", "orders"]
      property: /description
""",
    )
    write_policy(
        policy_b,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: second
    match:
      change: changed
      object:
        kind: table
        path: ["sales", "orders"]
      property: /description
  - id: first
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
""",
    )
    identity_a = drift_policy_identity(load_drift_policy(policy_a).to_identity_dict()).digest
    identity_b = drift_policy_identity(load_drift_policy(policy_b).to_identity_dict()).digest
    assert identity_a == identity_b


def test_policy_identity_ignores_yaml_comments_and_path(tmp_path: Path) -> None:
    policy_a = tmp_path / "dir-a" / "policy.yaml"
    policy_b = tmp_path / "dir-b" / "policy.yaml"
    policy_a.parent.mkdir(parents=True)
    policy_b.parent.mkdir(parents=True)
    content = """# comment
drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: allow-description
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
"""
    write_policy(policy_a, content)
    write_policy(policy_b, content + "# trailing comment\n")
    identity_a = drift_policy_identity(load_drift_policy(policy_a).to_identity_dict()).digest
    identity_b = drift_policy_identity(load_drift_policy(policy_b).to_identity_dict()).digest
    assert identity_a == identity_b


def test_equivalent_inputs_produce_identical_drift_bytes(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_policy(policy_path, _description_policy("allow-description"))
    policy = load_drift_policy(policy_path)
    comparison = build_comparison_result(
        build_snapshot(description=None),
        build_snapshot(description="updated"),
    )
    first = canonical_drift_json(build_drift_result(comparison, policy))
    second = canonical_drift_json(build_drift_result(comparison, policy))
    assert first == second
