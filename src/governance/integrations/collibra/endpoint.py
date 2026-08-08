"""Shared Collibra endpoint canonicalization for live adapter and target context."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_base_url(base_url: str) -> str:
    """Return canonical ``scheme://netloc`` target used by live Collibra clients.

    Path, query, and fragment are intentionally dropped. Embedded credentials
    are rejected. This is the single normalizer for runtime and identity.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("collibra_base_url is required for live mode")
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("collibra_base_url must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("collibra_base_url must not embed credentials")
    return f"{parsed.scheme}://{parsed.netloc}"
