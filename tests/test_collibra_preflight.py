"""Read-only Collibra preflight: transport gate before credentials."""

from __future__ import annotations

import json
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
    CODE_AUTH_CONFIG,
    CODE_AUTH_FAILURE,
    CODE_EMBEDDED_CREDENTIALS,
    CODE_MALFORMED_URL,
    CODE_MOCK_MODE,
    CODE_OPERATIONAL,
    CODE_READ_PERMISSION,
    CODE_REMOTE_HTTP,
    CODE_TOKEN_URL_INVALID,
    CODE_WRITES_NOT_PROBED,
    STATUS_INCOMPATIBLE,
    STATUS_NOT_VERIFIED,
    STATUS_OPERATIONAL_FAILURE,
    STATUS_VERIFIED,
    PreflightReport,
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


def _assert_zero_http_incompatible(
    report: PreflightReport,
    transport: CountingTransport,
    *,
    code: str,
) -> None:
    assert report.overall == STATUS_INCOMPATIBLE
    assert report.writes_performed == 0
    assert transport.requests == []
    assert any(check.code == code for check in report.checks)
    blob = str(report.to_dict())
    assert SECRET not in blob
    assert CONTRACT_CLIENT_SECRET not in blob


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        (
            {"collibra_client_id": "", "collibra_client_secret": ""},
            CODE_AUTH_CONFIG,
        ),
        ({"collibra_client_secret": ""}, CODE_AUTH_CONFIG),
        (
            {
                "collibra_client_id": "",
                "collibra_client_secret": "",
                "collibra_username": "preflight-user",
            },
            CODE_AUTH_CONFIG,
        ),
        (
            {"collibra_username": "preflight-user", "collibra_password": "preflight-password"},
            CODE_AUTH_CONFIG,
        ),
        ({"collibra_oauth_scope": "api"}, CODE_AUTH_CONFIG),
        (
            {
                "collibra_token_url": "https://idp.example.invalid/oauth/token",
                "collibra_oauth_client_auth": "bogus",
            },
            CODE_AUTH_CONFIG,
        ),
        (
            {"collibra_token_url": "https://idp.example.invalid/oauth/token?audience=foo"},
            CODE_TOKEN_URL_INVALID,
        ),
    ],
)
def test_invalid_local_configuration_sends_nothing(
    overrides: dict[str, str],
    code: str,
) -> None:
    transport = CountingTransport()
    report = run_preflight(
        _settings(**overrides),
        mock_mapping_config(),
        transport=transport,
    )
    _assert_zero_http_incompatible(report, transport, code=code)


def test_https_token_401_is_incompatible_auth_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if request.method.upper() == "POST" and path.endswith("/oauth/v2/token"):
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(500, json={"error": "unreachable"})

    report = run_preflight(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
    )
    assert report.overall == STATUS_INCOMPATIBLE
    assert report.writes_performed == 0
    assert any(check.code == CODE_AUTH_FAILURE for check in report.checks)
    assert SECRET not in str(report.to_dict())


def test_https_read_403_is_permission_denied_without_mutations() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = urlparse(str(request.url)).path
        if request.method.upper() == "POST" and path.endswith("/oauth/v2/token"):
            return httpx.Response(
                200,
                json={"access_token": "https-token", "token_type": "Bearer", "expires_in": 3600},
            )
        if request.method.upper() == "GET":
            return httpx.Response(403, json={"error": "forbidden"})
        return httpx.Response(500, json={"error": "writes-forbidden"})

    report = run_preflight(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
    )
    assert report.overall == STATUS_INCOMPATIBLE
    assert any(check.code == CODE_READ_PERMISSION for check in report.checks)
    methods = {request.method.upper() for request in captured}
    assert "PATCH" not in methods
    assert "PUT" not in methods
    assert "DELETE" not in methods
    assert all(
        request.method.upper() != "POST"
        or urlparse(str(request.url)).path.endswith("/oauth/v2/token")
        for request in captured
    )


@pytest.mark.parametrize("status_code", [429, 503])
def test_token_429_or_5xx_is_operational_failure(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if request.method.upper() == "POST" and path.endswith("/oauth/v2/token"):
            return httpx.Response(status_code, json={"error": "unavailable"})
        return httpx.Response(500, json={"error": "unreachable"})

    report = run_preflight(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
    )
    assert report.overall == STATUS_OPERATIONAL_FAILURE
    assert any(check.code == CODE_OPERATIONAL for check in report.checks)
    assert all(check.code != CODE_AUTH_FAILURE for check in report.checks)
    assert report.writes_performed == 0


def test_token_timeout_is_operational_failure() -> None:
    class TimeoutTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            del request
            raise httpx.ConnectTimeout("timed out")

    report = run_preflight(
        _settings(),
        mock_mapping_config(),
        transport=TimeoutTransport(),
    )
    assert report.overall == STATUS_OPERATIONAL_FAILURE
    assert any(check.code == CODE_OPERATIONAL for check in report.checks)
    assert all(check.code != CODE_AUTH_FAILURE for check in report.checks)
    assert report.writes_performed == 0


def test_malformed_oauth_response_is_operational_not_auth_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if request.method.upper() == "POST" and path.endswith("/oauth/v2/token"):
            return httpx.Response(200, text="{not-json")
        return httpx.Response(500, json={"error": "unreachable"})

    report = run_preflight(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
    )
    assert report.overall == STATUS_OPERATIONAL_FAILURE
    assert any(check.code == CODE_OPERATIONAL for check in report.checks)
    assert all(check.code != CODE_AUTH_FAILURE for check in report.checks)


def test_application_info_5xx_is_operational_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if request.method.upper() == "POST" and path.endswith("/oauth/v2/token"):
            return httpx.Response(
                200,
                json={"access_token": "https-token", "token_type": "Bearer", "expires_in": 3600},
            )
        if request.method.upper() == "GET" and path.endswith("/application/info"):
            return httpx.Response(
                503,
                json={"error": "unavailable"},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(500, json={"error": "unreachable"})

    report = run_preflight(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
    )
    assert report.overall == STATUS_OPERATIONAL_FAILURE
    assert any(check.code == CODE_OPERATIONAL for check in report.checks)


def test_cli_invalid_auth_json(
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
    monkeypatch.setenv("COLLIBRA_BASE_URL", "https://collibra.example.invalid")
    monkeypatch.setenv("COLLIBRA_CLIENT_ID", "")
    monkeypatch.setenv("COLLIBRA_CLIENT_SECRET", "")
    monkeypatch.setenv("COLLIBRA_USERNAME", "")
    monkeypatch.setenv("COLLIBRA_PASSWORD", "")
    monkeypatch.setenv("COLLIBRA_BEARER_TOKEN", "")
    assert main(["preflight", "--config", str(config), "--format", "json"]) == 1
    payload = capsys.readouterr().out
    parsed = json.loads(payload)
    assert parsed["overall"] == "INCOMPATIBLE"
    assert parsed["writes_performed"] == 0
    assert any(
        check["code"] == "authentication_configuration_invalid" for check in parsed["checks"]
    )
    assert SECRET not in payload
    assert CONTRACT_CLIENT_SECRET not in payload
