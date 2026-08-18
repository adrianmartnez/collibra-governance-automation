"""Shared Collibra endpoint canonicalization for live adapter and target context."""

from __future__ import annotations

from typing import Literal
from urllib.parse import parse_qs, urlparse

TransportClass = Literal["https", "loopback_http", "remote_http"]

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def normalize_base_url(base_url: str) -> str:
    """Return canonical ``scheme://netloc`` target used by live Collibra clients.

    Path, query, and fragment are intentionally dropped. Embedded credentials
    are rejected. This is the single normalizer for runtime and identity.
    """
    parsed = _parse_absolute_http_url(base_url, field="collibra_base_url")
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_token_url(token_url: str) -> str:
    """Return a canonical absolute token URL without fragment or embedded secrets."""
    parsed = _parse_absolute_http_url(token_url, field="oauth token_url")
    if _query_contains_embedded_credentials(parsed.query):
        raise ValueError("oauth token_url must not embed credentials")
    query = f"?{parsed.query}" if parsed.query else ""
    path = parsed.path or ""
    return f"{parsed.scheme}://{parsed.netloc}{path}{query}"


def classify_transport(url: str) -> TransportClass:
    """Classify an absolute HTTP(S) URL for the OAuth transport-security gate."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute http(s) URL")
    if parsed.scheme == "https":
        return "https"
    host = (parsed.hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return "loopback_http"
    return "remote_http"


def require_oauth_transport(url: str) -> TransportClass:
    """Allow HTTPS or HTTP loopback; reject HTTP non-loopback before token POST."""
    transport = classify_transport(url)
    if transport == "remote_http":
        raise ValueError("oauth token endpoint must use HTTPS or HTTP loopback")
    return transport


def _parse_absolute_http_url(url: str, *, field: str):
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"{field} is required for live mode")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{field} must not embed credentials")
    return parsed


def _query_contains_embedded_credentials(query: str) -> bool:
    if not query:
        return False
    keys = {key.lower() for key in parse_qs(query, keep_blank_values=True)}
    return "client_id" in keys or "client_secret" in keys
