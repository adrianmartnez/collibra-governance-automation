"""Unit tests for policy_identity determinism and path independence."""

from __future__ import annotations

from pathlib import Path

from governance.config_contract import load_canonical_config
from governance.identity import policy_identity
from governance.policy import NormalizedPolicySet, load_normalized_policies

POLICY_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "policies"
CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def _write_config(tmp_path: Path, *, relative_files: list[str], bodies: dict[str, str]) -> Path:
    for relative, body in bodies.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    files_block = "\n".join(f"    - {item}" for item in relative_files)
    path = tmp_path / "governance.yaml"
    path.write_text(
        "\n".join(
            [
                'schema_version: "1"',
                "sources:",
                "  - id: primary",
                "    provider: postgresql",
                "    config:",
                "      source_name: governance-demo",
                "      connection:",
                "        database_url_env: DATABASE_URL",
                "targets:",
                "  - id: collibra",
                "    provider: collibra",
                "    config:",
                "      mode_env: COLLIBRA_MODE",
                "      mapping:",
                "        path: mapping.json",
                "policies:",
                "  files:",
                files_block,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _identity_for(config_path: Path):
    policy_set = load_normalized_policies(load_canonical_config(config_path))
    return policy_identity(policy_set.to_identity_dict())


def test_empty_policy_set_identity_is_deterministic() -> None:
    empty = NormalizedPolicySet()
    also_empty = NormalizedPolicySet(policies=())
    assert empty.to_identity_dict() == also_empty.to_identity_dict()
    assert "path" not in str(empty.to_identity_dict()).lower()
    a = policy_identity(empty.to_identity_dict())
    b = policy_identity(also_empty.to_identity_dict())
    assert a == b
    assert a.algorithm == "sha256"
    assert a.hashing_contract_version == "1"
    assert len(a.digest) == 64


def test_empty_config_policies_match_empty_set(tmp_path: Path) -> None:
    path = tmp_path / "governance.yaml"
    path.write_text(
        "\n".join(
            [
                'schema_version: "1"',
                "sources:",
                "  - id: primary",
                "    provider: postgresql",
                "    config:",
                "      source_name: governance-demo",
                "      connection:",
                "        database_url_env: DATABASE_URL",
                "policies:",
                "  files: []",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_normalized_policies(load_canonical_config(path))
    assert policy_identity(loaded.to_identity_dict()) == policy_identity(
        NormalizedPolicySet().to_identity_dict()
    )


def test_policy_identity_invariant_to_path_comments_order_and_whitespace(
    tmp_path: Path,
) -> None:
    base = (POLICY_FIXTURES / "tables_require_owner.yaml").read_text(encoding="utf-8")
    warning = (POLICY_FIXTURES / "tables_require_description_warning.yaml").read_text(
        encoding="utf-8"
    )

    a_root = tmp_path / "a"
    b_root = tmp_path / "b"
    config_a = _write_config(
        a_root,
        relative_files=["policies/owner.yaml", "policies/desc.yaml"],
        bodies={
            "policies/owner.yaml": base,
            "policies/desc.yaml": warning,
        },
    )
    # Renamed paths, reordered files, comments, and insignificant whitespace.
    commented_owner = (
        "# comment should not affect identity\n"
        + base.replace("severity: error", "severity:   error")
        + "\n# trailing\n"
    )
    reordered_warning = warning.replace(
        "id: tables-require-description",
        "id: tables-require-description",
    )
    config_b = _write_config(
        b_root,
        relative_files=["rules/description.yaml", "rules/owner.yaml"],
        bodies={
            "rules/owner.yaml": commented_owner,
            "rules/description.yaml": reordered_warning,
        },
    )

    assert _identity_for(config_a) == _identity_for(config_b)


def test_policy_identity_invariant_to_policy_declaration_order(tmp_path: Path) -> None:
    combined = """policy_schema: governance-policy
policy_version: "1"
policies:
  - id: zebra
    severity: warning
    rule:
      type: require_description
      select:
        kind: table
  - id: alpha
    severity: error
    rule:
      type: require_owner
      select:
        kind: table
"""
    reversed_order = """policy_schema: governance-policy
policy_version: "1"
policies:
  - id: alpha
    severity: error
    rule:
      type: require_owner
      select:
        kind: table
  - id: zebra
    severity: warning
    rule:
      type: require_description
      select:
        kind: table
"""
    config_a = _write_config(
        tmp_path / "order-a",
        relative_files=["policies/p.yaml"],
        bodies={"policies/p.yaml": combined},
    )
    config_b = _write_config(
        tmp_path / "order-b",
        relative_files=["policies/p.yaml"],
        bodies={"policies/p.yaml": reversed_order},
    )
    assert _identity_for(config_a) == _identity_for(config_b)
    identity_dict = load_normalized_policies(load_canonical_config(config_a)).to_identity_dict()
    ids = [item["id"] for item in identity_dict["policies"]]
    assert ids == sorted(ids)


def test_material_rule_change_changes_identity(tmp_path: Path) -> None:
    owner = (POLICY_FIXTURES / "tables_require_owner.yaml").read_text(encoding="utf-8")
    config_a = _write_config(
        tmp_path / "m1",
        relative_files=["policies/p.yaml"],
        bodies={"policies/p.yaml": owner},
    )
    changed = owner.replace("severity: error", "severity: warning")
    config_b = _write_config(
        tmp_path / "m2",
        relative_files=["policies/p.yaml"],
        bodies={"policies/p.yaml": changed},
    )
    assert _identity_for(config_a) != _identity_for(config_b)
