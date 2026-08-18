"""Collibra authentication boundary: static bearer and OAuth client-credentials."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from governance.integrations.collibra.adapters import CollibraAuthError
from governance.integrations.collibra.endpoint import (
    normalize_base_url,
    normalize_token_url,
    require_oauth_transport,
)

NATIVE_TOKEN_PATH = "/rest/oauth/v2/token"
DEFAULT_SKEW_SECONDS = 30.0
SKEW_TTL_FRACTION = 0.10
OAUTH_CLIENT_AUTH_POST = "client_secret_post"
OAUTH_CLIENT_AUTH_BASIC = "client_secret_basic"
OAuthClientAuth = Literal["client_secret_post", "client_secret_basic"]


class CollibraTokenProvider(Protocol):
    """Supplies a Bearer access token for per-request Authorization."""

    def get_access_token(self) -> str: ...

    def invalidate(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _CachedAccessToken:
    access_token: str
    acquired_at: float
    expires_in: float
    skew: float


def effective_skew_seconds(expires_in: float) -> float:
    """Return TTL-relative skew that is always in ``[0, expires_in)``."""
    if expires_in <= 0:
        raise ValueError("expires_in must be positive")
    return min(DEFAULT_SKEW_SECONDS, expires_in * SKEW_TTL_FRACTION)


def parse_oauth_client_auth(value: str | None) -> OAuthClientAuth:
    raw = (value or "").strip() or OAUTH_CLIENT_AUTH_POST
    if raw not in {OAUTH_CLIENT_AUTH_POST, OAUTH_CLIENT_AUTH_BASIC}:
        raise ValueError("oauth_client_auth must be client_secret_post or client_secret_basic")
    return raw  # type: ignore[return-value]


class StaticBearerProvider:
    """Caller-supplied Bearer token; no refresh or acquisition."""

    def __init__(self, token: str) -> None:
        cleaned = token.strip()
        if not cleaned:
            raise ValueError("collibra_bearer_token is required for bearer auth")
        self._token = cleaned

    def get_access_token(self) -> str:
        return self._token

    def invalidate(self) -> None:
        return None

    def __repr__(self) -> str:
        return "StaticBearerProvider(token='***')"


class _OAuthClientCredentialsProvider:
    """Shared cache + token POST for native Collibra and external IdP."""

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        timeout_seconds: float,
        client_auth: OAuthClientAuth,
        scope: str | None,
        transport: httpx.BaseTransport | None,
        monotonic_clock: Callable[[], float],
        kind: Literal["native", "external"],
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._client_auth = client_auth
        self._scope = scope
        self._kind = kind
        self._monotonic_clock = monotonic_clock
        self._cached: _CachedAccessToken | None = None
        self._client = httpx.Client(
            timeout=timeout_seconds,
            verify=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def invalidate(self) -> None:
        self._cached = None

    def get_access_token(self) -> str:
        now = self._monotonic_clock()
        cached = self._cached
        if cached is not None:
            reuse_until = cached.acquired_at + cached.expires_in - cached.skew
            if now < reuse_until:
                return cached.access_token
        token = self._fetch_token()
        self._cached = token
        return token.access_token

    def _fetch_token(self) -> _CachedAccessToken:
        headers: dict[str, str] = {"Content-Type": "application/x-www-form-urlencoded"}
        data: dict[str, str] = {"grant_type": "client_credentials"}
        auth: httpx.Auth | None = None
        if self._client_auth == OAUTH_CLIENT_AUTH_BASIC:
            auth = httpx.BasicAuth(self._client_id, self._client_secret)
        else:
            data["client_id"] = self._client_id
            data["client_secret"] = self._client_secret
        if self._scope:
            data["scope"] = self._scope
        acquired_at = self._monotonic_clock()
        try:
            response = self._client.post(
                self._token_url,
                data=data,
                headers=headers,
                auth=auth,
            )
        except httpx.TimeoutException:
            raise CollibraAuthError(
                "oauth token request timed out",
                operation="oauth_token",
                endpoint_path=_token_endpoint_label(self._kind),
            ) from None
        except httpx.HTTPError:
            raise CollibraAuthError(
                "oauth token request failed",
                operation="oauth_token",
                endpoint_path=_token_endpoint_label(self._kind),
            ) from None
        if response.status_code >= 400:
            raise CollibraAuthError(
                "oauth token request failed",
                operation="oauth_token",
                status_code=response.status_code,
                endpoint_path=_token_endpoint_label(self._kind),
            )
        try:
            payload = response.json()
        except ValueError:
            raise CollibraAuthError(
                "oauth token response is malformed",
                operation="oauth_token",
                status_code=response.status_code,
                endpoint_path=_token_endpoint_label(self._kind),
            ) from None
        return _parse_token_payload(payload, acquired_at=acquired_at)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(kind={self._kind!r}, "
            f"client_auth={self._client_auth!r}, client_secret='***')"
        )


class CollibraNativeOAuthProvider(_OAuthClientCredentialsProvider):
    """Collibra native ``POST {base}/rest/oauth/v2/token`` (client_secret_post)."""

    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        normalized = normalize_base_url(base_url)
        try:
            require_oauth_transport(normalized)
        except ValueError as exc:
            raise CollibraAuthError(
                _safe_oauth_transport_message(exc),
                operation="oauth_token",
                endpoint_path=NATIVE_TOKEN_PATH,
            ) from None
        super().__init__(
            token_url=f"{normalized}{NATIVE_TOKEN_PATH}",
            client_id=client_id,
            client_secret=client_secret,
            timeout_seconds=timeout_seconds,
            client_auth=OAUTH_CLIENT_AUTH_POST,
            scope=None,
            transport=transport,
            monotonic_clock=monotonic_clock or time.monotonic,
            kind="native",
        )


class ExternalIdpOAuthProvider(_OAuthClientCredentialsProvider):
    """External IdP token URL with client_secret_post or client_secret_basic."""

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        timeout_seconds: float,
        client_auth: str | None = None,
        scope: str | None = None,
        transport: httpx.BaseTransport | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        try:
            normalized = normalize_token_url(token_url)
            require_oauth_transport(normalized)
            parsed_auth = parse_oauth_client_auth(client_auth)
        except ValueError as exc:
            raise CollibraAuthError(
                _safe_oauth_transport_message(exc),
                operation="oauth_token",
                endpoint_path="token_endpoint",
            ) from None
        cleaned_scope = (scope or "").strip() or None
        super().__init__(
            token_url=normalized,
            client_id=client_id,
            client_secret=client_secret,
            timeout_seconds=timeout_seconds,
            client_auth=parsed_auth,
            scope=cleaned_scope,
            transport=transport,
            monotonic_clock=monotonic_clock or time.monotonic,
            kind="external",
        )


def _parse_token_payload(payload: Any, *, acquired_at: float) -> _CachedAccessToken:
    if not isinstance(payload, dict):
        raise CollibraAuthError(
            "oauth token response is malformed",
            operation="oauth_token",
            endpoint_path="token_endpoint",
        )
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise CollibraAuthError(
            "oauth token response is missing access_token",
            operation="oauth_token",
            endpoint_path="token_endpoint",
        )
    token_type = payload.get("token_type")
    if not isinstance(token_type, str) or token_type.strip().lower() != "bearer":
        raise CollibraAuthError(
            "oauth token_type must be Bearer",
            operation="oauth_token",
            endpoint_path="token_endpoint",
        )
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in <= 0:
        raise CollibraAuthError(
            "oauth token response has invalid expires_in",
            operation="oauth_token",
            endpoint_path="token_endpoint",
        )
    skew = effective_skew_seconds(float(expires_in))
    return _CachedAccessToken(
        access_token=access_token.strip(),
        acquired_at=acquired_at,
        expires_in=float(expires_in),
        skew=skew,
    )


def _token_endpoint_label(kind: Literal["native", "external"]) -> str:
    if kind == "native":
        return NATIVE_TOKEN_PATH
    return "token_endpoint"


def _safe_oauth_transport_message(exc: BaseException) -> str:
    text = str(exc).lower()
    if "embed" in text:
        return "oauth token_url must not embed credentials"
    if "https or http loopback" in text:
        return "oauth token endpoint must use HTTPS or HTTP loopback"
    if "client_secret_post" in text or "oauth_client_auth" in text:
        return "oauth_client_auth must be client_secret_post or client_secret_basic"
    if "absolute" in text:
        return "oauth token_url must be an absolute http(s) URL"
    return "oauth token endpoint is invalid"
