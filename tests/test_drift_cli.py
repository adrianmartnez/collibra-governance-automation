"""CLI tests for governance drift."""

from __future__ import annotations

import json
from pathlib import Path

from conftest_drift import (
    write_different_description_comparison,
    write_identical_comparison,
    write_policy,
)
from governance.cli import main
from governance.drift import canonical_drift_json


def _empty_policy_yaml() -> str:
    return """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
"""


def _description_policy_yaml() -> str:
    return """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: allow-source-description
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
  - id: allow-table-description
    match:
      change: changed
      object:
        kind: table
        path: ["sales", "orders"]
      property: /description
"""


def _output_sentinel() -> bytes:
    return b"DO-NOT-TOUCH\x00\xff"


def test_drift_cli_identical_exit_zero(tmp_path: Path, capsys) -> None:
    comparison = tmp_path / "cmp.json"
    write_identical_comparison(comparison)
    code = main(["drift", "--comparison", str(comparison)])
    assert code == 0
    out = capsys.readouterr().out
    assert "status=no_difference" in out
    assert "writes=0" in out


def test_drift_cli_expected_difference_exit_zero(tmp_path: Path) -> None:
    comparison = tmp_path / "cmp.json"
    policy = tmp_path / "policy.yaml"
    write_different_description_comparison(comparison)
    write_policy(policy, _description_policy_yaml())
    assert main(["drift", "--comparison", str(comparison), "--policy", str(policy)]) == 0


def test_drift_cli_unexpected_drift_exit_zero(tmp_path: Path) -> None:
    comparison = tmp_path / "cmp.json"
    policy = tmp_path / "policy.yaml"
    write_different_description_comparison(comparison)
    write_policy(policy, _empty_policy_yaml())
    assert main(["drift", "--comparison", str(comparison), "--policy", str(policy)]) == 0


def test_drift_cli_json_and_output(tmp_path: Path, capsys) -> None:
    comparison = tmp_path / "cmp.json"
    out_path = tmp_path / "drift.json"
    write_identical_comparison(comparison)
    code = main(
        [
            "drift",
            "--comparison",
            str(comparison),
            "--format",
            "json",
            "--output",
            str(out_path),
        ]
    )
    assert code == 0
    stdout = capsys.readouterr().out
    artifact = out_path.read_text(encoding="utf-8")
    assert stdout == artifact
    payload = json.loads(artifact)
    assert payload["status"] == "no_difference"
    assert payload["writes_performed"] == 0
    assert canonical_drift_json(payload) == artifact


def test_drift_cli_missing_policy_on_different_exit_4(tmp_path: Path, capsys) -> None:
    comparison = tmp_path / "cmp.json"
    write_different_description_comparison(comparison)
    code = main(["drift", "--comparison", str(comparison), "--format", "json"])
    assert code == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "missing_drift_policy"
    assert payload["errors"][0]["path"] == "/policy"


def test_drift_cli_usage_missing_comparison_exit_2() -> None:
    code = main(["drift"])
    assert code == 2


def test_drift_cli_failure_does_not_write_output(tmp_path: Path) -> None:
    comparison = tmp_path / "cmp.json"
    out_path = tmp_path / "drift.json"
    write_different_description_comparison(comparison)
    code = main(
        [
            "drift",
            "--comparison",
            str(comparison),
            "--output",
            str(out_path),
        ]
    )
    assert code == 4
    assert not out_path.exists()


def test_drift_cli_invalid_comparison_exit_4(tmp_path: Path, capsys) -> None:
    comparison = tmp_path / "cmp.json"
    comparison.write_text("{not-json", encoding="utf-8")
    code = main(["drift", "--comparison", str(comparison), "--format", "json"])
    assert code == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["code"] == "comparison_parse_error"


def test_drift_cli_complex_yaml_key_exit_4(tmp_path: Path) -> None:
    comparison = tmp_path / "cmp.json"
    policy = tmp_path / "policy.yaml"
    write_different_description_comparison(comparison)
    write_policy(
        policy,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
? [a, b]
: ignored
""",
    )
    assert main(["drift", "--comparison", str(comparison), "--policy", str(policy)]) == 4


def test_drift_cli_cyclic_yaml_exit_4(tmp_path: Path) -> None:
    comparison = tmp_path / "cmp.json"
    policy = tmp_path / "policy.yaml"
    write_different_description_comparison(comparison)
    write_policy(
        policy,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: cyclic
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
    expected:
      baseline: &cycle
        has_value: true
        value: *cycle
""",
    )
    assert main(["drift", "--comparison", str(comparison), "--policy", str(policy)]) == 4


def test_drift_cli_invalid_policy_does_not_write_output(tmp_path: Path) -> None:
    comparison = tmp_path / "cmp.json"
    policy = tmp_path / "policy.yaml"
    out_path = tmp_path / "drift.json"
    write_different_description_comparison(comparison)
    write_policy(
        policy,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
1: value
""",
    )
    code = main(
        [
            "drift",
            "--comparison",
            str(comparison),
            "--policy",
            str(policy),
            "--output",
            str(out_path),
        ]
    )
    assert code == 4
    assert not out_path.exists()


def test_drift_cli_human_rule_ids_remain_unambiguous(tmp_path: Path, capsys) -> None:
    comparison = tmp_path / "cmp.json"
    policy = tmp_path / "policy.yaml"
    write_different_description_comparison(comparison)
    write_policy(
        policy,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules:
  - id: "a,b"
    match:
      change: changed
      object:
        kind: data_source
        path: []
      property: /description
  - id: c
    match:
      change: changed
      object:
        kind: table
        path: ["sales", "orders"]
      property: /description
""",
    )
    code = main(["drift", "--comparison", str(comparison), "--policy", str(policy)])
    assert code == 0
    out = capsys.readouterr().out
    assert 'rule_ids=["a,b"]' in out
    assert 'rule_ids=["a","b,c"]' not in out


def test_drift_cli_expected_difference_json_status(tmp_path: Path, capsys) -> None:
    comparison = tmp_path / "cmp.json"
    policy = tmp_path / "policy.yaml"
    write_different_description_comparison(comparison)
    write_policy(policy, _description_policy_yaml())
    code = main(
        ["drift", "--comparison", str(comparison), "--policy", str(policy), "--format", "json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "expected_difference"
    assert payload["summary"]["unexpected_drift"] == 0


def test_drift_cli_failure_missing_policy_preserves_output(tmp_path: Path) -> None:
    comparison = tmp_path / "cmp.json"
    out_path = tmp_path / "out.json"
    write_different_description_comparison(comparison)
    sentinel = _output_sentinel()
    out_path.write_bytes(sentinel)
    code = main(["drift", "--comparison", str(comparison), "--output", str(out_path)])
    assert code == 4
    assert out_path.read_bytes() == sentinel


def test_drift_cli_failure_invalid_policy_preserves_output(tmp_path: Path) -> None:
    comparison = tmp_path / "cmp.json"
    policy = tmp_path / "policy.yaml"
    out_path = tmp_path / "out.json"
    write_different_description_comparison(comparison)
    write_policy(
        policy,
        """drift_schema: governance-drift-policy
drift_version: "1"
rules: []
1: bad
""",
    )
    sentinel = _output_sentinel()
    out_path.write_bytes(sentinel)
    code = main(
        [
            "drift",
            "--comparison",
            str(comparison),
            "--policy",
            str(policy),
            "--output",
            str(out_path),
        ]
    )
    assert code == 4
    assert out_path.read_bytes() == sentinel


def test_drift_cli_failure_invalid_comparison_preserves_output(tmp_path: Path) -> None:
    comparison = tmp_path / "bad.json"
    out_path = tmp_path / "out.json"
    comparison.write_text("{not-json", encoding="utf-8")
    sentinel = _output_sentinel()
    out_path.write_bytes(sentinel)
    code = main(["drift", "--comparison", str(comparison), "--output", str(out_path)])
    assert code == 4
    assert out_path.read_bytes() == sentinel
