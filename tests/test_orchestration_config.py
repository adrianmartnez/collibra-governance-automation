"""Version-aware runtime configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.config_contract import (
    UnsupportedConfigVersionError,
    load_canonical_config,
    load_canonical_config_v2,
)
from governance.orchestration.config import (
    V1RuntimeConfiguration,
    V2RuntimeConfiguration,
    load_runtime_configuration,
)

FIXTURES_V2 = Path(__file__).resolve().parent / "fixtures" / "governance_yaml_v2"
FIXTURES_V1 = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def test_load_runtime_configuration_v1() -> None:
    path = FIXTURES_V1 / "valid_minimal.yaml"
    runtime = load_runtime_configuration(path)
    assert isinstance(runtime, V1RuntimeConfiguration)
    assert runtime.kind == "1"
    assert runtime.canonical == load_canonical_config(path)


def test_load_runtime_configuration_v2() -> None:
    path = FIXTURES_V2 / "valid_minimal.yaml"
    runtime = load_runtime_configuration(path)
    assert isinstance(runtime, V2RuntimeConfiguration)
    assert runtime.kind == "2"
    assert runtime.loaded == load_canonical_config_v2(path)


def test_load_runtime_configuration_unsupported_version() -> None:
    with pytest.raises(UnsupportedConfigVersionError):
        load_runtime_configuration(FIXTURES_V1 / "invalid_schema_version.yaml")


def test_load_runtime_configuration_does_not_fallback_across_versions() -> None:
    v2_path = FIXTURES_V2 / "valid_minimal.yaml"
    runtime = load_runtime_configuration(v2_path)
    assert isinstance(runtime, V2RuntimeConfiguration)
    with pytest.raises(UnsupportedConfigVersionError):
        load_canonical_config(v2_path)
