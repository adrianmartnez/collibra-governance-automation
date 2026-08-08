"""Regression: runtime configuration => exit 4 for new commands (no operational I/O)."""

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
from governance.integrations.collibra import CollibraAdapterError
from governance.integrations.collibra.mock import MockCollibraAdapter
from governance.scanner import MetadataDiscoveryError

CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def _model() -> GovernanceModel:
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
                        ownership=Ownership(owner_name="postgres"),
                        schemas=(
                            Schema(
                                id=make_schema_id(source, database, schema),
                                name=schema,
                                database_id=make_database_id(source, database),
                                ownership=Ownership(owner_name="governance_owner"),
                                tables=(
                                    Table(
                                        id=table_id,
                                        name="customers",
                                        schema_id=make_schema_id(source, database, schema),
                                        description="customers",
                                        ownership=Ownership(owner_name="governance_owner"),
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
        relationships=(),
    )


def _write_workspace(
    tmp_path: Path,
    *,
    discrete_connection: bool = False,
    with_timeout_env: bool = False,
    with_bearer_env: bool = False,
) -> Path:
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    if discrete_connection:
        connection = "\n".join(
            [
                "      connection:",
                "        host_env: PGHOST",
                "        port_env: PGPORT",
                "        db_env: PGDATABASE",
                "        user_env: PGUSER",
                "        password_env: PGPASSWORD",
            ]
        )
    else:
        connection = "\n".join(
            [
                "      connection:",
                "        database_url_env: DATABASE_URL",
            ]
        )
    auth_lines = [
        "        base_url_env: COLLIBRA_BASE_URL",
        "        username_env: COLLIBRA_USERNAME",
        "        password_env: COLLIBRA_PASSWORD",
    ]
    if with_bearer_env:
        auth_lines.append("        bearer_token_env: COLLIBRA_BEARER_TOKEN")
    if with_timeout_env:
        auth_lines.append("        timeout_seconds_env: COLLIBRA_TIMEOUT_SECONDS")
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
                connection,
                "targets:",
                "  - id: collibra",
                "    provider: collibra",
                "    config:",
                "      mode_env: COLLIBRA_MODE",
                "      mapping:",
                "        path: mapping.json",
                "      auth:",
                *auth_lines,
                "policies:",
                "  files: []",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def _patch_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    values = {
        "DATABASE_URL": "postgresql://postgres:super-secret-password@localhost:5432/governance_demo",
        "PGHOST": "localhost",
        "PGPORT": "5432",
        "PGDATABASE": "governance_demo",
        "PGUSER": "postgres",
        "PGPASSWORD": "super-secret-password",
        "COLLIBRA_MODE": "mock",
        "COLLIBRA_BASE_URL": "https://example.invalid",
        "COLLIBRA_USERNAME": "collibra-user",
        "COLLIBRA_PASSWORD": "collibra-secret-password",
        "COLLIBRA_BEARER_TOKEN": "",
        "COLLIBRA_TIMEOUT_SECONDS": "10",
    }
    values.update(overrides)
    for key, value in values.items():
        if value == "":
            monkeypatch.setenv(key, "")
        else:
            monkeypatch.setenv(key, value)


def _patch_scanner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    boom: Exception | None = None,
) -> dict[str, int]:
    calls = {"count": 0}

    class FakeScanner:
        def __init__(self, settings) -> None:
            self.settings = settings

        def scan(self):
            calls["count"] += 1
            if boom is not None:
                raise boom
            return _model()

    monkeypatch.setattr("governance.cli.PostgresMetadataScanner", FakeScanner)
    return calls


def _patch_adapter(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {"count": 0, "read": 0, "writes": 0}

    def factory(settings, mapping_config, *, transport=None):
        calls["count"] = int(calls["count"]) + 1
        adapter = MockCollibraAdapter(mapping_config)
        original_read = adapter.read_remote_state

        def tracked_read(desired):
            calls["read"] = int(calls["read"]) + 1
            return original_read(desired)

        adapter.read_remote_state = tracked_read  # type: ignore[method-assign]
        return adapter

    monkeypatch.setattr("governance.cli.build_collibra_adapter", factory)
    return calls


def _assert_resolution_exit4(payload: dict, *, code: str) -> None:
    assert payload["ok"] is False
    assert payload["diagnostic_schema"] == "governance-config-resolution-diagnostics"
    assert payload["errors"]
    assert any(err["code"] == code for err in payload["errors"])
    text = json.dumps(payload)
    assert "super-secret" not in text
    assert "token-value" not in text
    assert "collibra-secret-password" not in text


def test_plan_live_incomplete_basic_auth_exit_4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(
        monkeypatch,
        COLLIBRA_MODE="live",
        COLLIBRA_USERNAME="user-only",
        COLLIBRA_PASSWORD="",
        COLLIBRA_BEARER_TOKEN="",
    )
    config = _write_workspace(tmp_path)
    scanner = _patch_scanner(monkeypatch)
    adapter = _patch_adapter(monkeypatch)
    out = tmp_path / "plan.gplan"

    assert (
        main(
            [
                "plan",
                "--config",
                str(config),
                "--output",
                str(out),
                "--format",
                "json",
            ]
        )
        == 4
    )
    payload = json.loads(capsys.readouterr().out)
    _assert_resolution_exit4(payload, code="runtime_configuration_invalid")
    assert scanner["count"] == 0
    assert adapter["count"] == 0
    assert adapter["read"] == 0
    assert not out.exists()


def test_apply_live_no_auth_exit_4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(
        monkeypatch,
        COLLIBRA_MODE="mock",
        COLLIBRA_USERNAME="u",
        COLLIBRA_PASSWORD="p",
        COLLIBRA_BEARER_TOKEN="",
    )
    config = _write_workspace(tmp_path)
    _patch_scanner(monkeypatch)
    _patch_adapter(monkeypatch)
    plan_path = tmp_path / "p.gplan"
    assert main(["plan", "--config", str(config), "--output", str(plan_path)]) == 0
    capsys.readouterr()

    _patch_env(
        monkeypatch,
        COLLIBRA_MODE="live",
        COLLIBRA_USERNAME="",
        COLLIBRA_PASSWORD="",
        COLLIBRA_BEARER_TOKEN="",
    )
    adapter = _patch_adapter(monkeypatch)
    scanner = _patch_scanner(monkeypatch)
    assert main(["apply", str(plan_path), "--config", str(config), "--format", "json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    _assert_resolution_exit4(payload, code="runtime_configuration_invalid")
    assert scanner["count"] == 0
    assert adapter["count"] == 0
    assert adapter["read"] == 0


def test_plan_live_basic_and_bearer_exit_4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(
        monkeypatch,
        COLLIBRA_MODE="live",
        COLLIBRA_USERNAME="user",
        COLLIBRA_PASSWORD="pass",
        COLLIBRA_BEARER_TOKEN="token-value",
    )
    config = _write_workspace(tmp_path, with_bearer_env=True)
    scanner = _patch_scanner(monkeypatch)
    adapter = _patch_adapter(monkeypatch)

    assert (
        main(
            [
                "plan",
                "--config",
                str(config),
                "--output",
                str(tmp_path / "x.gplan"),
                "--format",
                "json",
            ]
        )
        == 4
    )
    payload = json.loads(capsys.readouterr().out)
    _assert_resolution_exit4(payload, code="runtime_configuration_invalid")
    assert scanner["count"] == 0
    assert adapter["count"] == 0


def test_plan_invalid_timeout_env_exit_4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch, COLLIBRA_TIMEOUT_SECONDS="not-a-number")
    config = _write_workspace(tmp_path, with_timeout_env=True)
    scanner = _patch_scanner(monkeypatch)
    adapter = _patch_adapter(monkeypatch)

    assert (
        main(
            [
                "plan",
                "--config",
                str(config),
                "--output",
                str(tmp_path / "x.gplan"),
                "--format",
                "json",
            ]
        )
        == 4
    )
    payload = json.loads(capsys.readouterr().out)
    _assert_resolution_exit4(payload, code="runtime_configuration_invalid")
    assert any(
        err["path"] == "/targets/0/config/auth/timeout_seconds_env" for err in payload["errors"]
    )
    assert scanner["count"] == 0
    assert adapter["count"] == 0


def test_check_invalid_port_env_exit_4_no_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch, PGPORT="not-an-int")
    config = _write_workspace(tmp_path, discrete_connection=True)
    scanner = _patch_scanner(monkeypatch)
    adapter = _patch_adapter(monkeypatch)

    assert main(["check", "--config", str(config), "--format", "json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    _assert_resolution_exit4(payload, code="runtime_configuration_invalid")
    assert any(err["path"] == "/sources/0/config/connection/port_env" for err in payload["errors"])
    assert scanner["count"] == 0
    assert adapter["count"] == 0


def test_check_malformed_database_url_exit_4_no_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch, DATABASE_URL="not-a-postgres-url")
    config = _write_workspace(tmp_path)
    scanner = _patch_scanner(monkeypatch)
    adapter = _patch_adapter(monkeypatch)

    assert main(["check", "--config", str(config), "--format", "json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    _assert_resolution_exit4(payload, code="runtime_configuration_invalid")
    assert any(
        err["path"] == "/sources/0/config/connection/database_url_env" for err in payload["errors"]
    )
    assert scanner["count"] == 0
    assert adapter["count"] == 0


def test_check_genuine_postgres_failure_exit_1_operation_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    config = _write_workspace(tmp_path)
    _patch_scanner(monkeypatch, boom=MetadataDiscoveryError("PostgreSQL metadata discovery failed"))
    adapter = _patch_adapter(monkeypatch)

    assert main(["check", "--config", str(config), "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnostic_schema"] == "governance-operation-diagnostics"
    assert payload["ok"] is False
    assert adapter["count"] == 0


def test_plan_genuine_collibra_read_failure_exit_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(
        monkeypatch,
        COLLIBRA_MODE="live",
        COLLIBRA_USERNAME="user",
        COLLIBRA_PASSWORD="pass",
        COLLIBRA_BEARER_TOKEN="",
    )
    config = _write_workspace(tmp_path)
    _patch_scanner(monkeypatch)

    def boom_factory(settings, mapping_config, *, transport=None):
        class BoomAdapter:
            def read_remote_state(self, desired):
                raise CollibraAdapterError(
                    "remote read failed",
                    operation="read_remote_state",
                )

        return BoomAdapter()

    monkeypatch.setattr("governance.cli.build_collibra_adapter", boom_factory)

    assert (
        main(
            [
                "plan",
                "--config",
                str(config),
                "--output",
                str(tmp_path / "x.gplan"),
                "--format",
                "json",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnostic_schema"] == "governance-operation-diagnostics"
    assert payload["ok"] is False
