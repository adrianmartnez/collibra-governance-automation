"""Unit tests for CanonicalConfig.authority identity and profiles."""

from __future__ import annotations

from pathlib import Path

from governance.config_contract import load_canonical_config
from governance.identity import config_identity

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def _base_yaml_lines() -> list[str]:
    return [
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
        "      auth:",
        "        base_url_env: COLLIBRA_BASE_URL",
        "        username_env: COLLIBRA_USERNAME",
        "        password_env: COLLIBRA_PASSWORD",
        "artifacts:",
        "  inventory_path: artifacts/metadata-inventory.json",
        "  snapshot_path: artifacts/governance-snapshot.json",
        "policies:",
        "  files: []",
    ]


def _write_config(tmp_path: Path, extra_lines: list[str] | None = None) -> Path:
    mapping = FIXTURES / "mapping.json"
    (tmp_path / "mapping.json").write_text(mapping.read_text(encoding="utf-8"), encoding="utf-8")
    lines = _base_yaml_lines()
    if extra_lines:
        lines.extend(extra_lines)
    path = tmp_path / "governance.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_legacy_config_identity_unchanged_without_authority(tmp_path: Path) -> None:
    legacy = load_canonical_config(FIXTURES / "valid_full.yaml")
    without = load_canonical_config(_write_config(tmp_path))
    assert "authority" not in legacy.identity_projection()
    assert "authority" not in without.identity_projection()
    assert config_identity(legacy.identity_projection()) == config_identity(
        without.identity_projection()
    )

    with_empty = load_canonical_config(_write_config(tmp_path, ["authority:", "  files: []"]))
    assert "authority" not in with_empty.identity_projection()
    assert config_identity(legacy.identity_projection()) == config_identity(
        with_empty.identity_projection()
    )


def test_authority_files_list_reorder_changes_config_identity(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(
        'authority_schema: governance-authority\nauthority_version: "1"\nrules: []\n',
        encoding="utf-8",
    )
    (tmp_path / "b.yaml").write_text(
        'authority_schema: governance-authority\nauthority_version: "1"\nrules: []\n',
        encoding="utf-8",
    )
    order_ab = load_canonical_config(
        _write_config(tmp_path, ["authority:", "  files:", "    - a.yaml", "    - b.yaml"])
    )
    order_ba = load_canonical_config(
        _write_config(tmp_path, ["authority:", "  files:", "    - b.yaml", "    - a.yaml"])
    )
    assert order_ab.authority.files == ("a.yaml", "b.yaml")
    assert order_ba.authority.files == ("b.yaml", "a.yaml")
    assert config_identity(order_ab.identity_projection()) != config_identity(
        order_ba.identity_projection()
    )


def test_mapping_key_reorder_same_config_identity(tmp_path: Path) -> None:
    mapping = FIXTURES / "mapping.json"
    (tmp_path / "mapping.json").write_text(mapping.read_text(encoding="utf-8"), encoding="utf-8")
    path_a = tmp_path / "a.yaml"
    path_b = tmp_path / "b.yaml"
    # Reordered mapping keys inside target config should normalize equivalently.
    path_a.write_text(
        "\n".join(
            _base_yaml_lines()
            + [
                "authority:",
                "  files:",
                "    - auth.yaml",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    path_b.write_text(
        'schema_version: "1"\n'
        "sources:\n"
        "  - id: primary\n"
        "    provider: postgresql\n"
        "    config:\n"
        "      connection:\n"
        "        database_url_env: DATABASE_URL\n"
        "      source_name: governance-demo\n"
        "targets:\n"
        "  - id: collibra\n"
        "    provider: collibra\n"
        "    config:\n"
        "      auth:\n"
        "        password_env: COLLIBRA_PASSWORD\n"
        "        username_env: COLLIBRA_USERNAME\n"
        "        base_url_env: COLLIBRA_BASE_URL\n"
        "      mapping:\n"
        "        path: mapping.json\n"
        "      mode_env: COLLIBRA_MODE\n"
        "artifacts:\n"
        "  snapshot_path: artifacts/governance-snapshot.json\n"
        "  inventory_path: artifacts/metadata-inventory.json\n"
        "policies:\n"
        "  files: []\n"
        "authority:\n"
        "  files:\n"
        "    - auth.yaml\n",
        encoding="utf-8",
    )
    (tmp_path / "auth.yaml").write_text(
        'authority_schema: governance-authority\nauthority_version: "1"\nrules: []\n',
        encoding="utf-8",
    )
    a = load_canonical_config(path_a)
    b = load_canonical_config(path_b)
    assert config_identity(a.identity_projection()) == config_identity(b.identity_projection())


def test_omit_empty_authority_from_to_dict(tmp_path: Path) -> None:
    empty = load_canonical_config(_write_config(tmp_path))
    assert "authority" not in empty.to_dict()
    assert "authority" not in empty.identity_projection()

    (tmp_path / "auth.yaml").write_text(
        'authority_schema: governance-authority\nauthority_version: "1"\nrules: []\n',
        encoding="utf-8",
    )
    with_files = load_canonical_config(
        _write_config(tmp_path, ["authority:", "  files:", "    - auth.yaml"])
    )
    assert with_files.to_dict()["authority"] == {"files": ["auth.yaml"]}
    assert with_files.identity_projection()["authority"] == {"files": ["auth.yaml"]}


def test_profile_inherit_and_replace_authority(tmp_path: Path) -> None:
    (tmp_path / "base-auth.yaml").write_text(
        'authority_schema: governance-authority\nauthority_version: "1"\nrules: []\n',
        encoding="utf-8",
    )
    (tmp_path / "ci-auth.yaml").write_text(
        'authority_schema: governance-authority\nauthority_version: "1"\nrules: []\n',
        encoding="utf-8",
    )
    path = _write_config(
        tmp_path,
        [
            "authority:",
            "  files:",
            "    - base-auth.yaml",
            "profiles:",
            "  inherit-only:",
            "    artifacts:",
            "      snapshot_path: artifacts/ci-snapshot.json",
            "  replace-authority:",
            "    authority:",
            "      files:",
            "        - ci-auth.yaml",
        ],
    )
    base = load_canonical_config(path)
    inherited = load_canonical_config(path, profile="inherit-only")
    replaced = load_canonical_config(path, profile="replace-authority")

    assert base.authority.files == ("base-auth.yaml",)
    assert inherited.authority.files == ("base-auth.yaml",)
    assert inherited.artifacts.snapshot_path == "artifacts/ci-snapshot.json"
    assert replaced.authority.files == ("ci-auth.yaml",)
    assert config_identity(base.identity_projection()) == config_identity(
        inherited.identity_projection()
    )
    assert config_identity(base.identity_projection()) != config_identity(
        replaced.identity_projection()
    )
