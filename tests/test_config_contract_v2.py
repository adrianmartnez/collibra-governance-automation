"""Focused tests for governance.yaml v2 load contract (#93)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.config_contract import (
    ConfigSchemaError,
    ConfigSemanticError,
    LoadedConfigV2,
    UnsupportedConfigVersionError,
    load_canonical_config,
    load_canonical_config_v2,
    validate_governance_config_v2,
)
from governance.identity import config_identity, config_identity_v2

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml_v2"
V1_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def test_load_minimal_returns_loaded_config_v2() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_minimal.yaml")
    assert isinstance(loaded, LoadedConfigV2)
    assert loaded.canonical.schema_version == "2"
    assert loaded.canonical.sources[0].id == "primary"
    assert loaded.canonical.sources[0].provider_id == "example.fixture"
    assert loaded.locations.source_locations["primary"] == "/sources/0"
    assert "config_root" not in loaded.canonical.to_dict()
    assert "locations" not in loaded.canonical.to_dict()


def test_shared_logical_id_across_source_and_target_allowed() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_shared_logical_id.yaml")
    assert loaded.canonical.sources[0].id == "catalog"
    assert loaded.canonical.targets[0].id == "catalog"
    assert loaded.locations.source_locations["catalog"] == "/sources/0"
    assert loaded.locations.target_locations["catalog"] == "/targets/0"


def test_duplicate_source_id_is_semantic_error() -> None:
    with pytest.raises(ConfigSemanticError) as exc:
        load_canonical_config_v2(FIXTURES / "invalid_duplicate_source_id.yaml")
    assert any("duplicate logical id" in error.message for error in exc.value.errors)


def test_canonical_sort_by_id_preserves_pre_sort_locations() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_multi_unsorted.yaml")
    assert [item.id for item in loaded.canonical.sources] == ["alpha", "zebra"]
    assert [item.id for item in loaded.canonical.targets] == ["beta", "omega"]
    # Document order before sort: zebra=0, alpha=1 / omega=0, beta=1
    assert loaded.locations.source_locations == {
        "zebra": "/sources/0",
        "alpha": "/sources/1",
    }
    assert loaded.locations.target_locations == {
        "omega": "/targets/0",
        "beta": "/targets/1",
    }


def test_env_refs_preserved_unresolved_in_canonical() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_with_env.yaml")
    config = loaded.canonical.sources[0].config
    assert config["token"] == {"$env": "PROVIDER_TOKEN"}
    assert config["nested"]["password"] == {"$env": "PROVIDER_PASSWORD"}


def test_invalid_env_shape_rejected_at_load() -> None:
    with pytest.raises(ConfigSemanticError) as exc:
        load_canonical_config_v2(FIXTURES / "invalid_env_shape.yaml")
    assert any("$env" in error.message for error in exc.value.errors)


def test_profile_overlay_updates_locations() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_profile.yaml", profile="ci")
    assert [item.id for item in loaded.canonical.sources] == ["primary", "secondary"]
    assert loaded.locations.source_locations["primary"] == "/sources/0"
    assert loaded.locations.source_locations["secondary"] == "/sources/1"


def test_locations_excluded_from_identity() -> None:
    loaded, identity = validate_governance_config_v2(FIXTURES / "valid_shared_logical_id.yaml")
    projection = loaded.canonical.identity_projection()
    assert "config_root" not in projection
    assert config_identity_v2(projection).to_dict() == identity
    # Locations must not affect digest: same projection without locations metadata.
    assert "locations" not in projection


def test_config_identity_v2_domain_separated_from_v1() -> None:
    payload = {"schema_version": "2", "sources": []}
    assert config_identity(payload) != config_identity_v2(payload)


def test_v1_loader_rejects_schema_version_2() -> None:
    with pytest.raises(UnsupportedConfigVersionError):
        load_canonical_config(FIXTURES / "valid_minimal.yaml")


def test_v2_loader_rejects_schema_version_1() -> None:
    with pytest.raises(UnsupportedConfigVersionError):
        load_canonical_config_v2(V1_FIXTURES / "valid_minimal.yaml")


def test_v1_fixtures_still_load() -> None:
    canonical = load_canonical_config(V1_FIXTURES / "valid_minimal.yaml")
    assert canonical.schema_version == "1"


def test_unknown_top_level_field_rejected() -> None:
    path = FIXTURES / "valid_minimal.yaml"
    text = path.read_text(encoding="utf-8") + "\nunknown_field: true\n"
    bad = path.parent / "_tmp_unknown.yaml"
    try:
        bad.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigSchemaError):
            load_canonical_config_v2(bad)
    finally:
        bad.unlink(missing_ok=True)


def test_schema_cache_v1_then_v2_no_contamination() -> None:
    v1 = load_canonical_config(V1_FIXTURES / "valid_minimal.yaml")
    v2 = load_canonical_config_v2(FIXTURES / "valid_minimal.yaml")
    again = load_canonical_config(V1_FIXTURES / "valid_minimal.yaml")
    assert v1.schema_version == "1"
    assert v2.canonical.schema_version == "2"
    assert again.schema_version == "1"
    assert again.identity_projection() == v1.identity_projection()


def test_schema_cache_v2_then_v1_no_contamination() -> None:
    v2 = load_canonical_config_v2(FIXTURES / "valid_minimal.yaml")
    v1 = load_canonical_config(V1_FIXTURES / "valid_minimal.yaml")
    again = load_canonical_config_v2(FIXTURES / "valid_minimal.yaml")
    assert v2.canonical.schema_version == "2"
    assert v1.schema_version == "1"
    assert again.canonical.schema_version == "2"
    assert again.canonical.identity_projection() == v2.canonical.identity_projection()


def test_rfc6901_escaped_keys_in_semantic_diagnostics() -> None:
    with pytest.raises(ConfigSemanticError) as exc:
        load_canonical_config_v2(FIXTURES / "invalid_pointer_keys_env.yaml")
    paths = {error.path for error in exc.value.errors}
    assert any(path.startswith("/sources/0/config/foo~1bar") for path in paths)
    assert not any("/sources/0/config/foo/bar" in path for path in paths)
    assert not any(path.startswith("/sources/0/config/foo~bar") for path in paths)


def test_rfc6901_keys_load_with_escaped_locations_only_for_entries() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_pointer_keys.yaml")
    config = loaded.canonical.sources[0].config
    assert config["foo/bar"] == {"$env": "TOKEN_A"}
    assert config["foo~bar"] == {"$env": "TOKEN_B"}
