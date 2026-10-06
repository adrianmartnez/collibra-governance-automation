"""Unit coverage for impact-sources-from-config Action flag."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.github_ci.paths import WorkspacePaths
from governance.github_ci.runner import ActionInputContractError, _parse_impact_sources


def test_parse_impact_sources_legacy_requires_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    paths = WorkspacePaths.from_env()
    with pytest.raises(ActionInputContractError, match="at least one"):
        _parse_impact_sources(
            paths,
            odcs_raw="",
            dbt_raw="",
            openlineage_raw="",
            allow_empty=False,
        )


def test_parse_impact_sources_allow_empty_for_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    paths = WorkspacePaths.from_env()
    assert (
        _parse_impact_sources(
            paths,
            odcs_raw="",
            dbt_raw="",
            openlineage_raw="",
            allow_empty=True,
        )
        == []
    )
