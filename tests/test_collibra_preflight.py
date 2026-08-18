"""Read-only Collibra preflight: transport gate before credentials."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from governance.cli import main
from governance.config import Settings
from governance.integrations.collibra import (
    mock_mapping_config,
    run_preflight,
)
from governance.integrations.collibra.preflight import (
    CODE_EMBEDDED_CREDENTIALS,
    CODE_MALFORMED_URL,
    CODE_MOCK_MODE,
    CODE_REMOTE_HTTP,
    CODE_WRITES_NOT_PROBED,
    STATUS_INCOMPATIBLE,
    STATUS_NOT_VERIFIED,
    STATUS_VERIFIED,
)
from support.collibra_contract_server import CONTRACT_CLIENT_SECRET

SECRET = "preflight-client-secret-do-not-leak"
CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


class CountingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raise AssertionError("preflight must not send HTTP")


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "governance_demo",
        "postgres_user": "postgres",
        "postgres_password": "postgres",
        "postgres_source_name": "governance-demo",
        "inventory_output_path": "artifacts/metadata-inventory.json",
        "collibra_mode": "live",
        "collibra_base_url": "https://collibra.example.invalid",
        "collibra_client_id": "preflight-client",
        "collibra_client_secret": SECRET,
        "collibra_timeout_seconds": 2.0,
    }
    values.update(overrides)
    return Settings(**values)


def _https_handler(request: httpx.Request) -> httpx.Response:
    path = urlparse(str(request.url)).path
    if request.method.upper() == "POST" and path.endswith("/oauth/v2/token"):
        return httpx.Response(
            200,
            json={"access_token": "https-token", "token_type": "Bearer", "expires_in": 3600},
        )
    if request.method.upper() == "GET" and path.endswith("/application/info"):
        return httpx.Response(200, json={"version": "https-mock"})
    if request.method.upper() == "GET" and any(
        part in path
        for part in ("/domains/", "/assetTypes/", "/attributeTypes/", "/relationTypes/")
    ):
        return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1]})
    if request.method.upper() in {"POST", "PATCH", "PUT", "DELETE"}:
        return httpx.Response(500, json={"error": "writes-forbidden"})
    return httpx.Response(404, json={"error": "not-found"})


def test_mock_mode_is_not_verified_and_sends_nothing() -> None:
    transport = CountingTransport()
    report = run_preflight(
        _settings(
            collibra_mode="mock",
            collibra_base_url="",
            collibra_client_id="",
            collibra_client_secret="",
        ),
        mock_mapping_config(),
        transport=transport,
    )
    assert report.overall == STATUS_NOT_VERIFIED
    assert report.writes_performed == 0
    assert transport.requests == []
    assert any(check.code == CODE_MOCK_MODE for check in report.checks)
    assert any(check.code == CODE_WRITES_NOT_PROBED for check in report.checks)


def test_http_non_loopback_is_incompatible_before_credentials() -> None:
    transport = CountingTransport()
    report = run_preflight(
        _settings(collibra_base_url="http://example.invalid"),
        mock_mapping_config(),
        transport=transport,
    )
    assert report.overall == STATUS_INCOMPATIBLE
    assert report.transport == "remote_http"
    assert report.writes_performed == 0
    assert transport.requests == []
    assert any(check.code == CODE_REMOTE_HTTP for check in report.checks)
    blob = str(report.to_dict())
    assert SECRET not in blob
    assert "Authorization" not in blob


def test_https_remote_read_only_verified() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _https_handler(request)

    report = run_preflight(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
    )
    assert report.overall == STATUS_VERIFIED
    assert report.transport == "https"
    assert report.writes_performed == 0
    methods = {request.method.upper() for request in captured}
    assert "PATCH" not in methods
    assert "DELETE" not in methods
    assert all(
        request.method.upper() != "POST"
        or urlparse(str(request.url)).path.endswith("/oauth/v2/token")
        for request in captured
    )
    assert any(check.code == CODE_WRITES_NOT_PROBED for check in report.checks)
    assert "write capability verified" not in str(report.to_dict()).lower()


def test_malformed_and_embedded_credentials_send_nothing() -> None:
    transport = CountingTransport()
    malformed = run_preflight(
        _settings(collibra_base_url="not-a-url"),
        mock_mapping_config(),
        transport=transport,
    )
    assert malformed.overall == STATUS_INCOMPATIBLE
    assert any(check.code == CODE_MALFORMED_URL for check in malformed.checks)
    embedded = run_preflight(
        _settings(collibra_base_url="https://user:super-secret@collibra.example.invalid"),
        mock_mapping_config(),
        transport=transport,
    )
    assert embedded.overall == STATUS_INCOMPATIBLE
    assert any(check.code == CODE_EMBEDDED_CREDENTIALS for check in embedded.checks)
    assert transport.requests == []
    assert "super-secret" not in str(embedded.to_dict())


def test_http_token_url_rejected_before_credentials() -> None:
    transport = CountingTransport()
    report = run_preflight(
        _settings(
            collibra_base_url="https://collibra.example.invalid",
            collibra_token_url="http://idp.example.invalid/oauth/token",
        ),
        mock_mapping_config(),
        transport=transport,
    )
    assert report.overall == STATUS_INCOMPATIBLE
    assert any(check.code == CODE_REMOTE_HTTP for check in report.checks)
    assert transport.requests == []


def _write_live_workspace(tmp_path: Path) -> Path:
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
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
                "        client_id_env: COLLIBRA_CLIENT_ID",
                "        client_secret_env: COLLIBRA_CLIENT_SECRET",
                "policies:",
                "  files: []",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def test_cli_http_remote_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _write_live_workspace(tmp_path)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/governance_demo",
    )
    monkeypatch.setenv("COLLIBRA_MODE", "live")
    monkeypatch.setenv("COLLIBRA_BASE_URL", "http://example.invalid")
    monkeypatch.setenv("COLLIBRA_CLIENT_ID", "preflight-client")
    monkeypatch.setenv("COLLIBRA_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("COLLIBRA_USERNAME", "")
    monkeypatch.setenv("COLLIBRA_PASSWORD", "")
    monkeypatch.setenv("COLLIBRA_BEARER_TOKEN", "")
    assert main(["preflight", "--config", str(config), "--format", "json"]) == 1
    payload = capsys.readouterr().out
    assert "INCOMPATIBLE" in payload
    assert "remote_http_rejected" in payload
    assert SECRET not in payload
    assert CONTRACT_CLIENT_SECRET not in payload


def test_cli_requires_config() -> None:
    assert main(["preflight"]) == 2
