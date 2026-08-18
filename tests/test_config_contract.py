"""Unit tests for governance.yaml contract validation and identities."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.cli import main
from governance.config_contract import (
    ConfigContractError,
    ConfigResolutionError,
    ConfigSchemaError,
    ConfigSemanticError,
    UnsupportedConfigVersionError,
    diagnostics_success,
    load_canonical_config,
    resolve_settings,
    validate_governance_config,
)
from governance.identity import config_identity

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def test_valid_minimal_and_empty_section_equivalence(tmp_path: Path) -> None:
    minimal = FIXTURES / "valid_minimal.yaml"
    with_empty = tmp_path / "with_empty.yaml"
    with_empty.write_text(
        minimal.read_text(encoding="utf-8")
        + "\npolicies:\n  files: []\n"
        + "artifacts:\n  inventory_path: artifacts/metadata-inventory.json\n"
        + "  snapshot_path: artifacts/governance-snapshot.json\n",
        encoding="utf-8",
    )
    a = load_canonical_config(minimal)
    b = load_canonical_config(with_empty)
    assert a.identity_projection() == b.identity_projection()
    assert config_identity(a.identity_projection()) == config_identity(b.identity_projection())
    assert a.artifacts.inventory_path == b.artifacts.inventory_path
    assert a.policies.files == ()


def test_unsupported_schema_version() -> None:
    with pytest.raises(UnsupportedConfigVersionError) as exc:
        load_canonical_config(FIXTURES / "invalid_schema_version.yaml")
    assert exc.value.errors[0].path == "/schema_version"
    assert exc.value.errors[0].code == "unsupported_config_version"


def test_empty_targets_rejected() -> None:
    with pytest.raises(ConfigContractError):
        load_canonical_config(FIXTURES / "invalid_targets_empty.yaml")


def test_unknown_field_rejected() -> None:
    with pytest.raises(ConfigSchemaError) as exc:
        load_canonical_config(FIXTURES / "invalid_unknown_field.yaml")
    assert any(error.code == "schema_validation_failed" for error in exc.value.errors)


def test_path_escape_rejected() -> None:
    with pytest.raises(ConfigSemanticError) as exc:
        load_canonical_config(FIXTURES / "invalid_path_escape.yaml")
    assert any("escape" in error.message for error in exc.value.errors)


def test_literal_password_rejected_by_schema() -> None:
    with pytest.raises(ConfigSchemaError) as exc:
        load_canonical_config(FIXTURES / "invalid_literal_password.yaml")
    messages = " ".join(error.message for error in exc.value.errors)
    assert "literal-secret" not in messages
    assert "password" not in messages.lower() or "unknown property" in messages


def test_profile_overlay_and_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    path = FIXTURES / "valid_full.yaml"
    base = load_canonical_config(path)
    ci = load_canonical_config(path, profile="ci")
    assert base.artifacts.snapshot_path == "artifacts/governance-snapshot.json"
    assert ci.artifacts.snapshot_path == "artifacts/ci-snapshot.json"

    monkeypatch.setenv("GOVERNANCE_PROFILE", "ci")
    from_env = load_canonical_config(path)
    assert from_env.artifacts.snapshot_path == "artifacts/ci-snapshot.json"

    cli_wins = load_canonical_config(path, profile="alt-artifacts")
    assert cli_wins.artifacts.snapshot_path == "artifacts/other-snapshot.json"


def test_unselected_profile_does_not_change_identity(tmp_path: Path) -> None:
    path = FIXTURES / "valid_full.yaml"
    a = load_canonical_config(path)
    text = path.read_text(encoding="utf-8")
    altered = text.replace(
        "snapshot_path: artifacts/ci-snapshot.json",
        "snapshot_path: artifacts/ci-snapshot-CHANGED.json",
    )
    assert altered != text
    tmp = tmp_path / "unselected.yaml"
    tmp.write_text(altered, encoding="utf-8")
    b = load_canonical_config(tmp)
    assert config_identity(a.identity_projection()) == config_identity(b.identity_projection())


def test_artifact_paths_excluded_from_config_identity() -> None:
    path = FIXTURES / "valid_full.yaml"
    a = load_canonical_config(path)
    b = load_canonical_config(path, profile="alt-artifacts")
    assert a.artifacts != b.artifacts
    assert config_identity(a.identity_projection()) == config_identity(b.identity_projection())


def test_material_mapping_path_changes_identity(tmp_path: Path) -> None:
    path = FIXTURES / "valid_full.yaml"
    a = load_canonical_config(path)
    text = path.read_text(encoding="utf-8").replace(
        "path: mapping.json",
        "path: other-mapping.json",
    )
    tmp = tmp_path / "mapping-change.yaml"
    tmp.write_text(text, encoding="utf-8")
    b = load_canonical_config(tmp)
    assert config_identity(a.identity_projection()) != config_identity(b.identity_projection())


def test_diagnostics_success_always_includes_identity() -> None:
    _canonical, identity = validate_governance_config(FIXTURES / "valid_minimal.yaml")
    payload = diagnostics_success(identity)
    assert payload["ok"] is True
    assert payload["config_identity"] == identity
    assert "digest" in payload["config_identity"]


def test_cli_config_validate_json(capsys: pytest.CaptureFixture[str]) -> None:
    path = FIXTURES / "valid_full.yaml"
    assert main(["config", "validate", "--config", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["diagnostic_schema"] == "governance-config-diagnostics"
    assert payload["config_identity"]["algorithm"] == "sha256"


def test_cli_config_validate_failure_json(capsys: pytest.CaptureFixture[str]) -> None:
    path = FIXTURES / "invalid_schema_version.yaml"
    assert main(["config", "validate", "--config", str(path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "config_identity" not in payload
    assert payload["errors"]


def test_invalid_config_skips_operational_io(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"scan": False}

    def boom_scan(self):  # noqa: ANN001
        called["scan"] = True
        raise AssertionError("scanner must not run")

    monkeypatch.setattr(
        "governance.cli.PostgresMetadataScanner.scan",
        boom_scan,
    )
    code = main(
        [
            "scan",
            "--config",
            str(FIXTURES / "invalid_schema_version.yaml"),
        ]
    )
    assert code == 1
    assert called["scan"] is False


def test_diff_requires_target_with_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "governance.cli.PostgresMetadataScanner.scan",
        lambda self: (_ for _ in ()).throw(AssertionError("no scan")),
    )
    code = main(["diff", "--config", str(FIXTURES / "valid_minimal.yaml"), "--mode", "mock"])
    assert code == 1


def test_resolve_settings_reads_database_url_from_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DATABASE_URL=postgresql://dotenv_user:dotenv_pass@dotenv-host:6543/dotenv_db\n",
        encoding="utf-8",
    )
    canonical = load_canonical_config(FIXTURES / "valid_minimal.yaml")
    settings = resolve_settings(canonical, dotenv_path=str(dotenv))
    assert settings.postgres_host == "dotenv-host"
    assert settings.postgres_port == 6543
    assert settings.postgres_db == "dotenv_db"
    assert settings.postgres_user == "dotenv_user"
    assert settings.postgres_password == "dotenv_pass"


def test_resolve_settings_process_env_wins_over_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DATABASE_URL=postgresql://dotenv_user:dotenv_pass@dotenv-host:6543/dotenv_db\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://process_user:process_pass@process-host:5432/process_db",
    )
    canonical = load_canonical_config(FIXTURES / "valid_minimal.yaml")
    settings = resolve_settings(canonical, dotenv_path=str(dotenv))
    assert settings.postgres_host == "process-host"
    assert settings.postgres_db == "process_db"
    assert settings.postgres_user == "process_user"
    assert settings.postgres_password == "process_pass"


def test_resolve_settings_reads_collibra_mode_from_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COLLIBRA_MODE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DATABASE_URL=postgresql://dotenv_user:dotenv_pass@dotenv-host:6543/dotenv_db\n"
        "COLLIBRA_MODE=live\n",
        encoding="utf-8",
    )
    canonical = load_canonical_config(FIXTURES / "valid_full.yaml")
    settings = resolve_settings(canonical, dotenv_path=str(dotenv))
    assert settings.collibra_mode == "live"
    assert settings.postgres_host == "dotenv-host"


def test_resolve_settings_explicit_environ_is_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DATABASE_URL=postgresql://dotenv_user:dotenv_pass@dotenv-host:6543/dotenv_db\n"
        "COLLIBRA_MODE=live\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://process_user:process_pass@process-host:5432/process_db",
    )
    monkeypatch.setenv("COLLIBRA_MODE", "live")
    canonical = load_canonical_config(FIXTURES / "valid_full.yaml")
    injected = {
        "DATABASE_URL": "postgresql://injected_user:injected_pass@injected-host:7432/injected_db",
        "COLLIBRA_MODE": "mock",
    }
    settings = resolve_settings(
        canonical,
        environ=injected,
        dotenv_path=str(dotenv),
    )
    assert settings.postgres_host == "injected-host"
    assert settings.postgres_port == 7432
    assert settings.postgres_db == "injected_db"
    assert settings.postgres_user == "injected_user"
    assert settings.postgres_password == "injected_pass"
    assert settings.collibra_mode == "mock"


def test_resolve_settings_missing_database_url_env_still_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("COLLIBRA_MODE=mock\n", encoding="utf-8")
    canonical = load_canonical_config(FIXTURES / "valid_minimal.yaml")
    with pytest.raises(ConfigResolutionError, match="DATABASE_URL"):
        resolve_settings(canonical, dotenv_path=str(dotenv))


def test_oauth_env_refs_are_additive_and_change_identity(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.json"
    mapping.write_text("{}", encoding="utf-8")
    yaml_text = """schema_version: "1"
sources:
  - id: primary
    provider: postgresql
    config:
      source_name: governance-demo
      connection:
        database_url_env: DATABASE_URL
targets:
  - id: collibra
    provider: collibra
    config:
      mapping:
        path: mapping.json
      auth:
        base_url_env: COLLIBRA_BASE_URL
        client_id_env: COLLIBRA_CLIENT_ID
        client_secret_env: COLLIBRA_CLIENT_SECRET
"""
    path = tmp_path / "governance.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    canonical = load_canonical_config(path)
    auth = canonical.targets[0].config.auth
    assert auth is not None
    assert auth.client_id_env == "COLLIBRA_CLIENT_ID"
    assert auth.client_secret_env == "COLLIBRA_CLIENT_SECRET"
    assert "client_id_env" in canonical.identity_projection()["targets"][0]["config"]["auth"]
    without_oauth = yaml_text.replace(
        "        client_id_env: COLLIBRA_CLIENT_ID\n"
        "        client_secret_env: COLLIBRA_CLIENT_SECRET\n",
        "",
    )
    other = tmp_path / "without-oauth.yaml"
    other.write_text(without_oauth, encoding="utf-8")
    base = load_canonical_config(other)
    assert config_identity(canonical.identity_projection()) != config_identity(
        base.identity_projection()
    )
