"""CLI tests for governance check (no Docker / real Postgres)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.cli import main
from governance.domain import (
    Column,
    Database,
    DataSource,
    GovernanceModel,
    Ownership,
    Schema,
    Table,
    make_column_id,
    make_database_id,
    make_datasource_id,
    make_schema_id,
    make_table_id,
)

POLICY_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "policies"
CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def _model(*, with_owners: bool = True, with_descriptions: bool = True) -> GovernanceModel:
    source = "governance-demo"
    database = "governance_demo"
    schema = "commerce"
    table_id = make_table_id(source, database, schema, "customers")
    col_id = make_column_id(source, database, schema, "customers", "customer_id")
    return GovernanceModel(
        data_sources=(
            DataSource(
                id=make_datasource_id(source),
                name=source,
                system_type="postgresql",
                databases=(
                    Database(
                        id=make_database_id(source, database),
                        name=database,
                        datasource_id=make_datasource_id(source),
                        schemas=(
                            Schema(
                                id=make_schema_id(source, database, schema),
                                name=schema,
                                database_id=make_database_id(source, database),
                                tables=(
                                    Table(
                                        id=table_id,
                                        name="customers",
                                        schema_id=make_schema_id(source, database, schema),
                                        description="ok" if with_descriptions else None,
                                        ownership=(
                                            Ownership(owner_name="owner") if with_owners else None
                                        ),
                                        columns=(
                                            Column(
                                                id=col_id,
                                                name="customer_id",
                                                data_type="uuid",
                                                ordinal_position=1,
                                                nullable=False,
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _write_workspace(tmp_path: Path, *, policy_names: list[str]) -> Path:
    policies_dir = tmp_path / "policies"
    policies_dir.mkdir()
    for name in policy_names:
        (policies_dir / name).write_text(
            (POLICY_FIXTURES / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    if policy_names:
        files_block = "  files:\n" + "\n".join(f"    - policies/{name}" for name in policy_names)
    else:
        files_block = "  files: []"
    config = tmp_path / "governance.yaml"
    config.write_text(
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
                "policies:",
                files_block,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def _patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:super-secret-password@localhost:5432/governance_demo",
    )
    monkeypatch.setenv("COLLIBRA_MODE", "mock")
    monkeypatch.setenv("COLLIBRA_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("COLLIBRA_BEARER_TOKEN", "secret-bearer-token")


def _patch_scanner(monkeypatch: pytest.MonkeyPatch, model: GovernanceModel) -> dict[str, int]:
    calls = {"count": 0}

    class FakeScanner:
        def __init__(self, settings) -> None:
            self.settings = settings

        def scan(self) -> GovernanceModel:
            calls["count"] += 1
            return model

    monkeypatch.setattr("governance.cli.PostgresMetadataScanner", FakeScanner)
    return calls


def _patch_adapter(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"count": 0, "read": 0}

    def factory(settings, mapping_config, *, transport=None):
        from governance.integrations.collibra import MockCollibraAdapter

        calls["count"] += 1
        adapter = MockCollibraAdapter(mapping_config)
        original = adapter.read_remote_state

        def tracked(desired):
            calls["read"] += 1
            return original(desired)

        adapter.read_remote_state = tracked  # type: ignore[method-assign]
        return adapter

    monkeypatch.setattr("governance.cli.build_collibra_adapter", factory)
    return calls


def test_check_requires_config_exit_2() -> None:
    assert main(["check"]) == 2


def test_check_pass_exit_0(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    config = _write_workspace(tmp_path, policy_names=["tables_require_owner.yaml"])
    scanner = _patch_scanner(monkeypatch, _model(with_owners=True))
    adapter = _patch_adapter(monkeypatch)

    assert main(["check", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "ok=true" in out
    assert scanner["count"] == 1
    assert adapter["count"] == 0
    assert adapter["read"] == 0


def test_check_errors_exit_3(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    config = _write_workspace(tmp_path, policy_names=["tables_require_owner.yaml"])
    _patch_scanner(monkeypatch, _model(with_owners=False))
    _patch_adapter(monkeypatch)

    assert main(["check", "--config", str(config), "--format", "json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["report_schema"] == "governance-policy-report"
    assert payload["violations"]
    assert all(item["severity"] == "error" for item in payload["violations"])


def test_check_warning_only_exit_0(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    config = _write_workspace(
        tmp_path,
        policy_names=["tables_require_description_warning.yaml"],
    )
    _patch_scanner(monkeypatch, _model(with_descriptions=False))
    _patch_adapter(monkeypatch)

    assert main(["check", "--config", str(config), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["violations"]
    assert all(item["severity"] == "warning" for item in payload["violations"])


def test_check_missing_database_url_exit_4_resolution_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("COLLIBRA_MODE", "mock")
    config = _write_workspace(tmp_path, policy_names=[])
    scanner = _patch_scanner(monkeypatch, _model())
    adapter = _patch_adapter(monkeypatch)

    assert main(["check", "--config", str(config), "--format", "json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostic_schema"] == "governance-config-resolution-diagnostics"
    assert payload["errors"]
    assert payload["errors"][0]["code"] == "environment_reference_unresolved"
    assert scanner["count"] == 0
    assert adapter["count"] == 0


def test_check_malformed_policy_exit_4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    config = _write_workspace(tmp_path, policy_names=["malformed.yaml"])
    scanner = _patch_scanner(monkeypatch, _model())
    adapter = _patch_adapter(monkeypatch)

    assert main(["check", "--config", str(config), "--format", "json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostic_schema"] == "governance-policy-diagnostics"
    assert scanner["count"] == 0
    assert adapter["count"] == 0


def test_check_missing_policy_file_exit_4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    config = _write_workspace(tmp_path, policy_names=[])
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "files: []",
            "files:\n    - policies/missing.yaml",
        ),
        encoding="utf-8",
    )
    scanner = _patch_scanner(monkeypatch, _model())

    assert main(["check", "--config", str(config), "--format", "json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnostic_schema"] == "governance-policy-diagnostics"
    assert any(error["code"] == "missing_policy_file" for error in payload["errors"])
    assert scanner["count"] == 0
