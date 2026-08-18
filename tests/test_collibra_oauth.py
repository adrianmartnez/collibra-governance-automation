"""OAuth client-credentials lifecycle tests for native Collibra and external IdP."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from governance.config import Settings
from governance.config_contract.resolve import ConfigResolutionError, validate_collibra_runtime
from governance.integrations.collibra import (
    CollibraAdapterError,
    CollibraAssetSpec,
    CollibraAuthError,
    LiveCollibraAdapter,
    mock_mapping_config,
)
from governance.integrations.collibra.auth import (
    DEFAULT_SKEW_SECONDS,
    SKEW_TTL_FRACTION,
    CollibraNativeOAuthProvider,
    ExternalIdpOAuthProvider,
    effective_skew_seconds,
)
from governance.integrations.collibra.endpoint import classify_transport, normalize_token_url

CLIENT_ID = "oauth-client-id-value"
CLIENT_SECRET = "oauth-client-secret-do-not-leak"
ACCESS_TOKEN = "oauth-access-token-do-not-leak"
NATIVE_TOKEN_PATH = "/rest/oauth/v2/token"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _OAuthCollibra:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_status = 200
        self.token_payload: Any = {
            "access_token": ACCESS_TOKEN,
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        self.token_raise: Exception | None = None
        self.api_statuses: list[int] = []
        self._asset_seq = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method.upper() == "POST" and (
            path.endswith(NATIVE_TOKEN_PATH) or path.endswith("/oauth/token")
        ):
            if self.token_raise is not None:
                raise self.token_raise
            if self.token_status >= 400:
                return httpx.Response(self.token_status, json={"error": "token"})
            if self.token_payload is None:
                return httpx.Response(200, content=b"not-json")
            return httpx.Response(200, json=self.token_payload)
        if self.api_statuses:
            status = self.api_statuses.pop(0)
            if status >= 400:
                return httpx.Response(status, json={"error": "api"})
        if request.method.upper() == "POST" and path.endswith("/assets"):
            self._asset_seq += 1
            return httpx.Response(201, json={"id": f"asset-{self._asset_seq}"})
        return httpx.Response(404, json={"error": "not found"})


def _settings(**overrides: Any) -> Settings:
    base = {
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "governance_demo",
        "postgres_user": "postgres",
        "postgres_password": "postgres",
        "postgres_source_name": "governance-demo",
        "inventory_output_path": "artifacts/metadata-inventory.json",
        "collibra_mode": "live",
        "collibra_base_url": "https://collibra.example.invalid",
        "collibra_timeout_seconds": 10.0,
        "collibra_client_id": CLIENT_ID,
        "collibra_client_secret": CLIENT_SECRET,
    }
    base.update(overrides)
    return Settings(**base)


def _adapter(
    fake: _OAuthCollibra,
    settings: Settings,
    *,
    clock: FakeClock | None = None,
) -> LiveCollibraAdapter:
    return LiveCollibraAdapter.from_settings(
        settings,
        mock_mapping_config(),
        transport=httpx.MockTransport(fake.handler),
        monotonic_clock=clock,
    )


def _create_asset(adapter: LiveCollibraAdapter) -> None:
    config = mock_mapping_config()
    adapter.create_asset(
        CollibraAssetSpec(
            local_id="db:demo/db",
            name="db",
            display_name="db",
            asset_type_ref=config.asset_type_refs["database"],
            domain_ref=config.domain_ref,
            attributes=(),
        )
    )


def _token_requests(fake: _OAuthCollibra) -> list[httpx.Request]:
    return [
        request
        for request in fake.requests
        if request.method.upper() == "POST"
        and (
            urlparse(str(request.url)).path.endswith(NATIVE_TOKEN_PATH)
            or urlparse(str(request.url)).path.endswith("/oauth/token")
        )
    ]


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode(), keep_blank_values=True)


def test_effective_skew_ttl_relative() -> None:
    assert effective_skew_seconds(3600) == DEFAULT_SKEW_SECONDS
    assert effective_skew_seconds(60) == 6.0
    assert effective_skew_seconds(30) == 3.0
    assert effective_skew_seconds(10) == 1.0
    for ttl in (3600, 60, 30, 10):
        skew = effective_skew_seconds(ttl)
        assert 0 <= skew < ttl
        assert skew == min(DEFAULT_SKEW_SECONDS, ttl * SKEW_TTL_FRACTION)


def test_native_token_request_shape() -> None:
    fake = _OAuthCollibra()
    adapter = _adapter(fake, _settings())
    _create_asset(adapter)
    tokens = _token_requests(fake)
    assert len(tokens) == 1
    request = tokens[0]
    assert urlparse(str(request.url)).path == NATIVE_TOKEN_PATH
    assert "application/x-www-form-urlencoded" in request.headers.get("Content-Type", "")
    form = _form(request)
    assert form == {
        "grant_type": ["client_credentials"],
        "client_id": [CLIENT_ID],
        "client_secret": [CLIENT_SECRET],
    }
    assert "scope" not in form
    assert any(
        item.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}" for item in fake.requests
    )


def test_external_client_secret_post_and_scope_absent() -> None:
    fake = _OAuthCollibra()
    adapter = _adapter(
        fake,
        _settings(collibra_token_url="https://idp.example.invalid/oauth/token"),
    )
    _create_asset(adapter)
    tokens = _token_requests(fake)
    assert len(tokens) == 1
    form = _form(tokens[0])
    assert form["grant_type"] == ["client_credentials"]
    assert form["client_id"] == [CLIENT_ID]
    assert form["client_secret"] == [CLIENT_SECRET]
    assert "scope" not in form
    assert not tokens[0].headers.get("Authorization", "").startswith("Basic ")


def test_external_client_secret_post_sends_scope_when_present() -> None:
    fake = _OAuthCollibra()
    adapter = _adapter(
        fake,
        _settings(
            collibra_token_url="https://idp.example.invalid/oauth/token",
            collibra_oauth_scope="catalog.read",
        ),
    )
    _create_asset(adapter)
    form = _form(_token_requests(fake)[0])
    assert form["scope"] == ["catalog.read"]


def test_external_client_secret_basic() -> None:
    fake = _OAuthCollibra()
    adapter = _adapter(
        fake,
        _settings(
            collibra_token_url="https://idp.example.invalid/oauth/token",
            collibra_oauth_client_auth="client_secret_basic",
            collibra_oauth_scope="catalog.read",
        ),
    )
    _create_asset(adapter)
    request = _token_requests(fake)[0]
    assert request.headers.get("Authorization", "").startswith("Basic ")
    form = _form(request)
    assert form == {"grant_type": ["client_credentials"], "scope": ["catalog.read"]}
    assert "client_id" not in form
    assert "client_secret" not in form


def test_native_rejects_scope_and_token_url_extras() -> None:
    fake = _OAuthCollibra()
    with pytest.raises(CollibraAuthError, match="native oauth rejects"):
        _adapter(fake, _settings(collibra_oauth_scope="catalog.read"))
    assert _token_requests(fake) == []
    with pytest.raises(CollibraAuthError, match="native oauth rejects"):
        _adapter(fake, _settings(collibra_oauth_client_auth="client_secret_basic"))
    assert _token_requests(fake) == []


def test_token_reuse_one_post_many_api() -> None:
    fake = _OAuthCollibra()
    clock = FakeClock()
    adapter = _adapter(fake, _settings(), clock=clock)
    _create_asset(adapter)
    _create_asset(adapter)
    _create_asset(adapter)
    assert len(_token_requests(fake)) == 1


@pytest.mark.parametrize(
    ("expires_in", "reuse_until"),
    [(3600, 3570.0), (60, 54.0), (30, 27.0), (10, 9.0)],
)
def test_ttl_relative_reuse_windows(expires_in: int, reuse_until: float) -> None:
    fake = _OAuthCollibra()
    fake.token_payload = {
        "access_token": ACCESS_TOKEN,
        "token_type": "Bearer",
        "expires_in": expires_in,
    }
    clock = FakeClock(start=0.0)
    adapter = _adapter(fake, _settings(), clock=clock)
    _create_asset(adapter)
    clock.advance(reuse_until - 0.001)
    _create_asset(adapter)
    assert len(_token_requests(fake)) == 1
    clock.advance(0.001)
    _create_asset(adapter)
    assert len(_token_requests(fake)) == 2


def test_near_expiry_refreshes_when_remaining_equals_skew() -> None:
    fake = _OAuthCollibra()
    fake.token_payload = {
        "access_token": ACCESS_TOKEN,
        "token_type": "bearer",
        "expires_in": 60,
    }
    clock = FakeClock(start=0.0)
    adapter = _adapter(fake, _settings(), clock=clock)
    _create_asset(adapter)
    clock.advance(54.0)
    _create_asset(adapter)
    assert len(_token_requests(fake)) == 2


def test_401_triggers_one_reacquisition() -> None:
    fake = _OAuthCollibra()
    fake.api_statuses = [401]
    adapter = _adapter(fake, _settings())
    _create_asset(adapter)
    assert len(_token_requests(fake)) == 2
    api_posts = [
        request
        for request in fake.requests
        if request.method == "POST" and urlparse(str(request.url)).path.endswith("/assets")
    ]
    assert len(api_posts) == 2


def test_second_401_is_terminal() -> None:
    fake = _OAuthCollibra()
    fake.api_statuses = [401, 401]
    adapter = _adapter(fake, _settings())
    with pytest.raises(CollibraAdapterError, match="status_code=401"):
        _create_asset(adapter)
    assert len(_token_requests(fake)) == 2


def test_403_is_terminal_without_refresh() -> None:
    fake = _OAuthCollibra()
    fake.api_statuses = [403]
    adapter = _adapter(fake, _settings())
    with pytest.raises(CollibraAdapterError, match="status_code=403"):
        _create_asset(adapter)
    assert len(_token_requests(fake)) == 1


def test_malformed_missing_and_invalid_token_payloads() -> None:
    fake = _OAuthCollibra()
    fake.token_payload = None
    adapter = _adapter(fake, _settings())
    with pytest.raises(CollibraAuthError, match="malformed"):
        _create_asset(adapter)

    fake = _OAuthCollibra()
    fake.token_payload = {"token_type": "Bearer", "expires_in": 3600}
    adapter = _adapter(fake, _settings())
    with pytest.raises(CollibraAuthError, match="missing access_token"):
        _create_asset(adapter)

    fake = _OAuthCollibra()
    fake.token_payload = {
        "access_token": ACCESS_TOKEN,
        "token_type": "mac",
        "expires_in": 3600,
    }
    adapter = _adapter(fake, _settings())
    with pytest.raises(CollibraAuthError, match="token_type"):
        _create_asset(adapter)

    fake = _OAuthCollibra()
    fake.token_payload = {
        "access_token": ACCESS_TOKEN,
        "token_type": "Bearer",
        "expires_in": 0,
    }
    adapter = _adapter(fake, _settings())
    with pytest.raises(CollibraAuthError, match="expires_in"):
        _create_asset(adapter)

    fake = _OAuthCollibra()
    fake.token_payload = {
        "access_token": ACCESS_TOKEN,
        "token_type": "Bearer",
        "expires_in": True,
    }
    adapter = _adapter(fake, _settings())
    with pytest.raises(CollibraAuthError, match="expires_in"):
        _create_asset(adapter)


def test_token_endpoint_failure_and_timeout() -> None:
    fake = _OAuthCollibra()
    fake.token_status = 503
    adapter = _adapter(fake, _settings())
    with pytest.raises(CollibraAuthError, match="token request failed"):
        _create_asset(adapter)

    fake = _OAuthCollibra()
    fake.token_raise = httpx.TimeoutException("slow")
    adapter = _adapter(fake, _settings())
    with pytest.raises(CollibraAuthError, match="timed out"):
        _create_asset(adapter)


def test_native_https_and_http_loopback_allowed() -> None:
    fake = _OAuthCollibra()
    adapter = _adapter(fake, _settings(collibra_base_url="https://collibra.example.invalid"))
    _create_asset(adapter)
    assert len(_token_requests(fake)) == 1

    fake = _OAuthCollibra()
    adapter = _adapter(fake, _settings(collibra_base_url="http://127.0.0.1:8080"))
    _create_asset(adapter)
    assert len(_token_requests(fake)) == 1


def test_native_http_remote_rejected_before_token_post() -> None:
    fake = _OAuthCollibra()
    with pytest.raises(CollibraAuthError, match="HTTPS or HTTP loopback") as exc_info:
        _adapter(fake, _settings(collibra_base_url="http://collibra.example.invalid"))
    assert _token_requests(fake) == []
    text = str(exc_info.value)
    assert CLIENT_ID not in text
    assert CLIENT_SECRET not in text


def test_external_https_and_http_loopback_allowed() -> None:
    fake = _OAuthCollibra()
    adapter = _adapter(
        fake,
        _settings(collibra_token_url="https://idp.example.invalid/oauth/token"),
    )
    _create_asset(adapter)
    assert len(_token_requests(fake)) == 1

    fake = _OAuthCollibra()
    adapter = _adapter(
        fake,
        _settings(collibra_token_url="http://127.0.0.1:9/oauth/token"),
    )
    _create_asset(adapter)
    assert len(_token_requests(fake)) == 1


def test_external_http_remote_rejected_before_token_post() -> None:
    fake = _OAuthCollibra()
    with pytest.raises(CollibraAuthError, match="HTTPS or HTTP loopback") as exc_info:
        _adapter(
            fake,
            _settings(collibra_token_url="http://idp.example.invalid/oauth/token"),
        )
    assert fake.requests == []
    text = str(exc_info.value)
    assert CLIENT_ID not in text
    assert CLIENT_SECRET not in text


def test_embedded_credentials_on_token_url_rejected() -> None:
    userinfo_url = (
        "https://USERINFO_USER_CANARY:USERINFO_PASSWORD_CANARY@idp.example.invalid/oauth/token"
    )
    settings = _settings(collibra_token_url=userinfo_url)
    assert "USERINFO_USER_CANARY" not in repr(settings)
    assert "USERINFO_PASSWORD_CANARY" not in repr(settings)
    assert "USERINFO_PASSWORD_CANARY" not in str(settings.redacted())
    assert settings.redacted()["collibra_token_url"] == ("https://idp.example.invalid/oauth/token")

    fake = _OAuthCollibra()
    with pytest.raises(CollibraAuthError, match="embed credentials") as exc_info:
        _adapter(fake, settings)
    assert fake.requests == []
    text = str(exc_info.value)
    assert "USERINFO_USER_CANARY" not in text
    assert "USERINFO_PASSWORD_CANARY" not in text
    assert CLIENT_ID not in text
    assert CLIENT_SECRET not in text

    with pytest.raises(ConfigResolutionError, match="embed credentials") as exc_info:
        validate_collibra_runtime(settings)
    assert "USERINFO_PASSWORD_CANARY" not in str(exc_info.value)


def test_token_url_query_rejected_and_not_leaked() -> None:
    query_canary = "QUERY_SECRET_CANARY"
    token_url = f"https://idp.example.invalid/oauth/token?api_key={query_canary}"
    settings = _settings(collibra_token_url=token_url)
    assert query_canary not in repr(settings)
    assert query_canary not in str(settings.redacted())
    assert settings.redacted()["collibra_token_url"] == ("https://idp.example.invalid/oauth/token")

    fake = _OAuthCollibra()
    with pytest.raises(CollibraAuthError, match="query string") as exc_info:
        _adapter(fake, settings)
    assert fake.requests == []
    assert query_canary not in str(exc_info.value)
    assert query_canary not in repr(exc_info.value)

    with pytest.raises(ConfigResolutionError, match="query string") as exc_info:
        validate_collibra_runtime(settings)
    assert query_canary not in str(exc_info.value)
    assert query_canary not in repr(exc_info.value)

    fake = _OAuthCollibra()
    with pytest.raises(CollibraAuthError, match="query string") as exc_info:
        _adapter(
            fake,
            _settings(
                collibra_token_url=(
                    f"https://idp.example.invalid/oauth/token?client_secret={CLIENT_SECRET}"
                )
            ),
        )
    assert fake.requests == []
    assert CLIENT_SECRET not in str(exc_info.value)


def test_normalize_token_url_accepts_https_without_query() -> None:
    assert (
        normalize_token_url("https://idp.example.invalid/oauth/token")
        == "https://idp.example.invalid/oauth/token"
    )
    with pytest.raises(ValueError, match="query string") as exc_info:
        normalize_token_url("https://idp.example.invalid/oauth/token?api_key=QUERY_SECRET_CANARY")
    assert "QUERY_SECRET_CANARY" not in str(exc_info.value)


def test_provider_repr_hides_secrets() -> None:
    provider = CollibraNativeOAuthProvider(
        base_url="https://collibra.example.invalid",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        timeout_seconds=10.0,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
    assert CLIENT_SECRET not in repr(provider)
    assert ACCESS_TOKEN not in repr(provider)
    external = ExternalIdpOAuthProvider(
        token_url="https://idp.example.invalid/oauth/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        timeout_seconds=10.0,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
    assert CLIENT_SECRET not in repr(external)


def test_settings_redacted_masks_client_secret() -> None:
    settings = _settings()
    redacted = settings.redacted()
    assert redacted["collibra_client_secret"] == "***"
    assert CLIENT_SECRET not in str(redacted)
    assert ACCESS_TOKEN not in str(redacted)


def test_validate_collibra_runtime_oauth_xor_and_legacy() -> None:
    validate_collibra_runtime(_settings())
    validate_collibra_runtime(
        _settings(
            collibra_client_id="",
            collibra_client_secret="",
            collibra_username="u",
            collibra_password="p",
        )
    )
    validate_collibra_runtime(
        _settings(
            collibra_client_id="",
            collibra_client_secret="",
            collibra_bearer_token="t",
        )
    )
    with pytest.raises(ConfigResolutionError, match="exactly one auth"):
        validate_collibra_runtime(_settings(collibra_username="u", collibra_password="p"))
    with pytest.raises(ConfigResolutionError, match="exactly one auth"):
        validate_collibra_runtime(
            _settings(
                collibra_client_id="",
                collibra_client_secret="",
                collibra_username="u",
                collibra_password="p",
                collibra_bearer_token="t",
            )
        )


def test_classify_transport() -> None:
    assert classify_transport("https://collibra.example.invalid") == "https"
    assert classify_transport("http://127.0.0.1:8080") == "loopback_http"
    assert classify_transport("http://localhost/rest") == "loopback_http"
    assert classify_transport("http://[::1]/") == "loopback_http"
    assert classify_transport("http://collibra.example.invalid") == "remote_http"


def test_adapter_repr_omits_secrets() -> None:
    fake = _OAuthCollibra()
    adapter = _adapter(fake, _settings())
    text = repr(adapter)
    assert CLIENT_SECRET not in text
    assert ACCESS_TOKEN not in text
    assert adapter._auth_mode == "oauth"
