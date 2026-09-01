"""RFC6901 pointer helpers for drift policy diagnostics."""

from __future__ import annotations


def rfc6901_escape_segment(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def join_pointer(base: str, segment: str) -> str:
    if base == "/":
        return f"/{rfc6901_escape_segment(segment)}"
    if not base.startswith("/"):
        raise ValueError("pointer base must start with '/'")
    return f"{base}/{rfc6901_escape_segment(segment)}"
