"""Unit tests for authority GaC load / parse / sample validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from governance.authority.errors import (
    CODE_AMBIGUOUS,
    CODE_DUPLICATE,
    AuthorityParseError,
    AuthoritySemanticError,
)
from governance.authority.load import load_normalized_authority
from governance.authority.parse import parse_authority_yaml
from governance.authority.schema import validate_authority_structure
from governance.config_contract import ConfigContractError, load_canonical_config
from governance.config_contract.models import CanonicalConfig
from governance.domain.authority import NormalizedAuthorityPolicySet

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "sample" / "authority" / "metadata-authority.example.yaml"
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


def _write_authority(path: Path, rules: list[dict] | None = None) -> Path:
    document = {
        "authority_schema": "governance-authority",
        "authority_version": "1",
        "rules": [] if rules is None else rules,
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_load_empty_rules_file(tmp_path: Path) -> None:
    _write_authority(tmp_path / "empty.yaml", [])
    canonical = _minimal_config(tmp_path, ["empty.yaml"])
    policy = load_normalized_authority(canonical)
    assert isinstance(policy, NormalizedAuthorityPolicySet)
    assert policy.rules == ()
    assert policy.content_identity().algorithm == "sha256"


def test_duplicate_id_rejected(tmp_path: Path) -> None:
    rule = {
        "id": "dup",
        "select": {"kind": "table", "property": "/description"},
        "authority": {"provider_type": "odcs"},
    }
    _write_authority(tmp_path / "a.yaml", [rule])
    _write_authority(tmp_path / "b.yaml", [dict(rule)])
    canonical = _minimal_config(tmp_path, ["a.yaml", "b.yaml"])
    with pytest.raises(AuthoritySemanticError) as exc:
        load_normalized_authority(canonical)
    assert any(error.code == CODE_DUPLICATE for error in exc.value.errors)


def test_same_selector_different_target_ambiguous(tmp_path: Path) -> None:
    rules = [
        {
            "id": "odcs-desc",
            "select": {"kind": "table", "property": "/description"},
            "authority": {"provider_type": "odcs"},
        },
        {
            "id": "dbt-desc",
            "select": {"kind": "table", "property": "/description"},
            "authority": {"provider_type": "dbt"},
        },
    ]
    _write_authority(tmp_path / "ambig.yaml", rules)
    canonical = _minimal_config(tmp_path, ["ambig.yaml"])
    with pytest.raises(AuthoritySemanticError) as exc:
        load_normalized_authority(canonical)
    assert any(error.code == CODE_AMBIGUOUS for error in exc.value.errors)
    from governance.authority.errors import map_authority_exception_to_config_diagnostics

    mapped = map_authority_exception_to_config_diagnostics(exc.value, canonical=canonical)
    assert mapped
    assert all(item.path == "/authority/files/0" for item in mapped)
    assert all(item.code == "semantic_validation_failed" for item in mapped)
    assert all(item.path != "/authority/files" for item in mapped)


def test_cross_file_ambiguity_indexed_diagnostics(tmp_path: Path) -> None:
    _write_authority(
        tmp_path / "a.yaml",
        [
            {
                "id": "odcs-desc",
                "select": {"kind": "table", "property": "/description"},
                "authority": {"provider_type": "odcs"},
            }
        ],
    )
    _write_authority(
        tmp_path / "b.yaml",
        [
            {
                "id": "dbt-desc",
                "select": {"kind": "table", "property": "/description"},
                "authority": {"provider_type": "dbt"},
            }
        ],
    )
    canonical = _minimal_config(tmp_path, ["a.yaml", "b.yaml"])
    with pytest.raises(AuthoritySemanticError) as exc:
        load_normalized_authority(canonical)
    from governance.authority.errors import map_authority_exception_to_config_diagnostics

    mapped = map_authority_exception_to_config_diagnostics(exc.value, canonical=canonical)
    paths = sorted({item.path for item in mapped})
    assert paths == ["/authority/files/0", "/authority/files/1"]
    assert all(item.code == "semantic_validation_failed" for item in mapped)
    assert "/authority/files" not in {item.path for item in mapped}


def test_empty_and_whitespace_authority_refs_rejected(tmp_path: Path) -> None:
    mapping = CONFIG_FIXTURES / "mapping.json"
    (tmp_path / "mapping.json").write_text(mapping.read_text(encoding="utf-8"), encoding="utf-8")
    for bad in ('""', '"   "'):
        path = tmp_path / "bad.yaml"
        path.write_text(
            'schema_version: "1"\n'
            "sources:\n"
            "  - id: primary\n"
            "    provider: postgresql\n"
            "    config:\n"
            "      source_name: governance-demo\n"
            "      connection:\n"
            "        database_url_env: DATABASE_URL\n"
            "authority:\n"
            "  files:\n"
            f"    - {bad}\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigContractError):
            load_canonical_config(path)


def test_sample_file_validates_and_loads(tmp_path: Path) -> None:
    assert SAMPLE.is_file()
    document = parse_authority_yaml(SAMPLE, source="sample")
    validate_authority_structure(document, source="sample")
    dest = tmp_path / "metadata-authority.example.yaml"
    dest.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    canonical = _minimal_config(tmp_path, ["metadata-authority.example.yaml"])
    policy = load_normalized_authority(canonical)
    assert len(policy.rules) == 3
    text = SAMPLE.read_text(encoding="utf-8").lower()
    for forbidden in ("password", "token", "secret", "bearer"):
        assert forbidden not in text


def test_yaml_safe_load_rejects_python_tags(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text(
        "authority_schema: governance-authority\n"
        'authority_version: "1"\n'
        "rules: !!python/object/apply:os.system ['echo pwned']\n",
        encoding="utf-8",
    )
    with pytest.raises(AuthorityParseError):
        parse_authority_yaml(path, source="unsafe.yaml")


def test_parse_uses_safe_load_not_full_load() -> None:
    import inspect

    source = inspect.getsource(parse_authority_yaml)
    assert "yaml.safe_load" in source
    assert "yaml.load(" not in source
    assert "FullLoader" not in source
    assert "UnsafeLoader" not in source
