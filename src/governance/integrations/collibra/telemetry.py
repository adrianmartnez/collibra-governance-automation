"""Secret-safe Collibra operational telemetry.

Telemetry is execution-specific and must never enter plan, snapshot, graph,
hash, or impact identities. Sink failures never change governance decisions.

Default sink is NullSink. Set COLLIBRA_TELEMETRY=jsonl for JSON lines on stderr
or COLLIBRA_TELEMETRY_PATH. Events never go to CLI stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Protocol, TextIO
from uuid import uuid4

TELEMETRY_ENV = "COLLIBRA_TELEMETRY"
TELEMETRY_PATH_ENV = "COLLIBRA_TELEMETRY_PATH"

ALLOWED_KEYS = frozenset(
    {
        "additional_characteristic_count",
        "attempt",
        "batch_count",
        "batch_index",
        "correlation_id",
        "duration_ms",
        "endpoint_family",
        "endpoint_path",
        "execution_mode",
        "job_id",
        "normalized_job_state",
        "normalized_result",
        "operation",
        "outcome",
        "remote_result",
        "remote_state",
        "resource_count",
        "status",
        "writes_performed",
    }
)

REMOTE_STATES = frozenset({"WAITING", "RUNNING", "CANCELING", "COMPLETED", "CANCELED", "ERROR"})
REMOTE_RESULTS = frozenset({"NOT_SET", "SUCCESS", "COMPLETED_WITH_ERROR", "FAILURE", "ABORTED"})

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_SECRET_MARKERS = (
    "authorization",
    "bearer",
    "access_token",
    "client_secret",
    "password",
    "token=",
)

_correlation_id: ContextVar[str | None] = ContextVar("collibra_correlation_id", default=None)
_sink: ContextVar[TelemetrySink | None] = ContextVar("collibra_telemetry_sink", default=None)


class TelemetrySink(Protocol):
    def emit(self, event: dict[str, Any]) -> None: ...


class NullSink:
    """Default sink: discard events."""

    def emit(self, event: dict[str, Any]) -> None:
        del event


class RecordingSink:
    """In-memory sink for tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))


class JsonlSink:
    """Append one canonical JSON object per line. Never writes CLI stdout."""

    def __init__(self, path: str | None = None) -> None:
        self._path = (path or "").strip() or None

    def emit(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if self._path:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")
            return
        stream: TextIO = sys.stderr
        stream.write(line)
        stream.write("\n")


def sink_from_environ(environ: dict[str, str] | None = None) -> TelemetrySink:
    env = os.environ if environ is None else environ
    mode = (env.get(TELEMETRY_ENV) or "").strip().lower()
    if mode != "jsonl":
        return NullSink()
    return JsonlSink((env.get(TELEMETRY_PATH_ENV) or "").strip() or None)


def current_correlation_id() -> str | None:
    return _correlation_id.get()


@contextmanager
def bound_sink(sink: TelemetrySink):
    """Bind a sink without starting a new execution correlation."""
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def get_sink() -> TelemetrySink:
    bound = _sink.get()
    if bound is not None:
        return bound
    return sink_from_environ()


@contextmanager
def execution_scope(*, execution_mode: str = "", sink: TelemetrySink | None = None):
    """Bind a correlation id for one logical Collibra execution."""
    cid_token: Token[str | None] = _correlation_id.set(str(uuid4()))
    if sink is not None:
        active = sink
    else:
        existing = _sink.get()
        active = existing if existing is not None else sink_from_environ()
    sink_token: Token[TelemetrySink | None] = _sink.set(active)
    try:
        emit(operation="execution_start", execution_mode=execution_mode)
        yield current_correlation_id()
    finally:
        _correlation_id.reset(cid_token)
        _sink.reset(sink_token)


def emit(**fields: Any) -> None:
    """Emit one allowlisted event. Never raises to the caller."""
    try:
        sink = get_sink()
        if isinstance(sink, NullSink):
            return
        sink.emit(build_event(fields))
    except Exception:
        return


def build_event(fields: dict[str, Any]) -> dict[str, Any]:
    """Return a redacted, allowlisted event. Used by tests and emit()."""
    event: dict[str, Any] = {}
    correlation = current_correlation_id()
    if correlation:
        event["correlation_id"] = correlation
    for key, value in fields.items():
        if key not in ALLOWED_KEYS or value is None:
            continue
        event[key] = _sanitize_value(key, value)
    return event


def endpoint_template(value: str) -> str:
    """Return a query-free path with UUIDs replaced by {id}."""
    raw = (value or "").split("?", 1)[0]
    if "://" in raw:
        from urllib.parse import urlparse

        parsed = urlparse(raw)
        raw = parsed.path or raw
    return _UUID_RE.sub("{id}", raw)


def _sanitize_value(key: str, value: Any) -> Any:
    if key in {"remote_state", "remote_result"}:
        text = str(value)
        allowed = REMOTE_STATES if key == "remote_state" else REMOTE_RESULTS
        return text if text in allowed else "unknown"
    if key == "endpoint_path" and isinstance(value, str):
        return endpoint_template(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        return int(value) if key == "duration_ms" else value
    text = str(value)
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return "***"
    return text
