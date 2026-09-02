"""Multi-file authority loader order independence."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest_history import write_authority_yaml
from governance.authority.errors import (
    CODE_AMBIGUOUS,
    CODE_DUPLICATE,
    CODE_PARSE,
    AuthorityError,
)
from governance.authority.load import load_normalized_authority, load_normalized_authority_files
from governance.config_contract import load_canonical_config
from governance.config_contract.models import CanonicalConfig

CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def _minimal_config(tmp_path: Path, authority_files: list[str]) -> CanonicalConfig:
    mapping = CONFIG_FIXTURES / "mapping.json"
    (tmp_path / "mapping.json").write_text(mapping.read_text(encoding="utf-8"), encoding="utf-8")
    files_yaml = "\n".join(f"    - {item}" for item in authority_files)
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
                "      auth:",
                "        base_url_env: COLLIBRA_BASE_URL",
                "authority:",
                "  files:",
                files_yaml,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return load_canonical_config(path)


def test_authority_files_order_independent(tmp_path: Path) -> None:
    a = write_authority_yaml(
        tmp_path / "a.yaml",
        rule_id="rule-a",
        kind="table",
        property_pointer="/description",
        provider_type="odcs",
        source_ref="contract-a",
    )
    b = write_authority_yaml(
        tmp_path / "b.yaml",
        rule_id="rule-b",
        kind="column",
        property_pointer="/description",
        provider_type="dbt",
        source_ref="model.orders",
    )
    forward = load_normalized_authority_files([a, b])
    reverse = load_normalized_authority_files([b, a])
    assert forward.content_identity() == reverse.content_identity()
    assert len(forward.rules) == 2


def test_authority_files_empty() -> None:
    empty = load_normalized_authority_files([])
    assert empty.rules == ()


def test_authority_files_missing(tmp_path: Path) -> None:
    try:
        load_normalized_authority_files([tmp_path / "missing.yaml"])
        raise AssertionError("expected AuthorityError")
    except AuthorityError as exc:
        assert any(item.code == "missing_authority_file" for item in exc.errors)


def test_duplicate_ids_across_files_rejected(tmp_path: Path) -> None:
    a = write_authority_yaml(tmp_path / "a.yaml", rule_id="shared-id")
    b = write_authority_yaml(
        tmp_path / "b.yaml",
        rule_id="shared-id",
        kind="column",
        property_pointer="/name",
        provider_type="dbt",
        source_ref="model.x",
    )
    with pytest.raises(AuthorityError) as exc_info:
        load_normalized_authority_files([a, b])
    assert any(item.code == CODE_DUPLICATE for item in exc_info.value.errors)


def test_same_selector_different_target_across_files_rejected(tmp_path: Path) -> None:
    a = write_authority_yaml(
        tmp_path / "a.yaml",
        rule_id="odcs-desc",
        provider_type="odcs",
        source_ref="contract-a",
    )
    b = write_authority_yaml(
        tmp_path / "b.yaml",
        rule_id="dbt-desc",
        provider_type="dbt",
        source_ref="model.orders",
    )
    with pytest.raises(AuthorityError) as exc_info:
        load_normalized_authority_files([a, b])
    assert any(item.code == CODE_AMBIGUOUS for item in exc_info.value.errors)


def test_invalid_yaml_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("rules: [\n  - id: broken\n", encoding="utf-8")
    with pytest.raises(AuthorityError) as exc_info:
        load_normalized_authority_files([path])
    assert any(item.code == CODE_PARSE for item in exc_info.value.errors)


def test_load_normalized_authority_canonical_delegates(tmp_path: Path) -> None:
    write_authority_yaml(tmp_path / "a.yaml", rule_id="rule-a")
    write_authority_yaml(
        tmp_path / "b.yaml",
        rule_id="rule-b",
        kind="column",
        property_pointer="/name",
        provider_type="dbt",
        source_ref="model.orders",
    )
    canonical = _minimal_config(tmp_path, ["a.yaml", "b.yaml"])
    via_config = load_normalized_authority(canonical)
    via_files = load_normalized_authority_files([tmp_path / "a.yaml", tmp_path / "b.yaml"])
    assert via_config.content_identity() == via_files.content_identity()
    assert len(via_config.rules) == 2


def test_authority_yaml_safe_load_tag_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text(
        "authority_schema: governance-authority\n"
        'authority_version: "1"\n'
        "rules: !!python/object/apply:os.system ['echo pwned']\n",
        encoding="utf-8",
    )
    with pytest.raises(AuthorityError):
        load_normalized_authority_files([path])
