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
import time
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
        "submission_state",
        "writes_performed",
    }
)

REMOTE_STATES = frozenset({"WAITING", "RUNNING", "CANCELING", "COMPLETED", "CANCELED", "ERROR"})
REMOTE_RESULTS = frozenset({"NOT_SET", "SUCCESS", "COMPLETED_WITH_ERROR", "FAILURE", "ABORTED"})
SUBMISSION_STATES = frozenset({"not_attempted", "submitted", "unknown"})
_TERMINAL_OUTCOMES = frozenset({"success", "failure", "error"})

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_ID_PATH_PREFIXES = (
    "/rest/2.0/jobs/",
    "/rest/2.0/assets/",
    "/rest/2.0/attributes/",
    "/rest/2.0/relations/",
    "/rest/2.0/domains/",
    "/rest/2.0/assetTypes/",
    "/rest/2.0/attributeTypes/",
    "/rest/2.0/relationTypes/",
    "/rest/2.0/import/results/",
    "/rest/2.0/import/synchronize/",
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
_execution_started: ContextVar[float | None] = ContextVar(
    "collibra_execution_started", default=None
)
_execution_outcome_emitted: ContextVar[bool] = ContextVar(
    "collibra_execution_outcome_emitted", default=False
)


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
def execution_scope(
    *,
    execution_mode: str = "",
    sink: TelemetrySink | None = None,
    default_outcome: str | None = None,
    default_writes_performed: int | None = None,
):
    """Bind a correlation id for one logical Collibra execution.

    Nested scopes reuse the root correlation ID, root start time, and
    terminal-outcome state. They do not emit a second ``execution_start``.
    The sink may be rebound for the nested duration.
    """
    existing_correlation = current_correlation_id()
    owns_correlation = existing_correlation is None
    cid_token: Token[str | None] | None = None
    started_token: Token[float | None] | None = None
    emitted_token: Token[bool] | None = None
    if owns_correlation:
        cid_token = _correlation_id.set(str(uuid4()))
        started_token = _execution_started.set(time.monotonic())
        emitted_token = _execution_outcome_emitted.set(False)
    if sink is not None:
        active = sink
    else:
        existing = _sink.get()
        active = existing if existing is not None else sink_from_environ()
    sink_token: Token[TelemetrySink | None] = _sink.set(active)
    try:
        if owns_correlation:
            emit(operation="execution_start", execution_mode=execution_mode)
        yield current_correlation_id()
    except Exception:
        if owns_correlation and not _execution_outcome_emitted.get():
            finish_execution(outcome="error", execution_mode=execution_mode)
        raise
    else:
        if (
            owns_correlation
            and not _execution_outcome_emitted.get()
            and default_outcome is not None
        ):
            finish_execution(
                outcome=default_outcome,
                execution_mode=execution_mode,
                writes_performed=default_writes_performed,
            )
    finally:
        _sink.reset(sink_token)
        if owns_correlation:
            if emitted_token is not None:
                _execution_outcome_emitted.reset(emitted_token)
            if started_token is not None:
                _execution_started.reset(started_token)
            if cid_token is not None:
                _correlation_id.reset(cid_token)


def finish_execution(
    *,
    outcome: str,
    execution_mode: str = "",
    writes_performed: int | None = None,
) -> None:
    """Emit the single terminal ``execution_outcome`` for the active correlation."""
    if current_correlation_id() is None:
        return
    if _execution_outcome_emitted.get():
        return
    _execution_outcome_emitted.set(True)
    started = _execution_started.get()
    duration_ms = 0 if started is None else int(max(0.0, (time.monotonic() - started) * 1000))
    safe_outcome = outcome if outcome in _TERMINAL_OUTCOMES else "error"
    emit(
        operation="execution_outcome",
        execution_mode=execution_mode,
        outcome=safe_outcome,
        duration_ms=duration_ms,
        writes_performed=writes_performed,
    )


def emit(**fields: Any) -> None:
    """Emit one allowlisted event. Never raises to the caller."""
    try:
        if fields.get("operation") == "execution_outcome" and current_correlation_id():
            _execution_outcome_emitted.set(True)
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
    """Return a query-free path with dynamic segments replaced by {id}."""
    raw = (value or "").split("?", 1)[0]
    if "://" in raw:
        from urllib.parse import urlparse

        parsed = urlparse(raw)
        raw = parsed.path or raw
    raw = _UUID_RE.sub("{id}", raw)
    for prefix in _ID_PATH_PREFIXES:
        if raw.startswith(prefix) and len(raw) > len(prefix):
            rest = raw[len(prefix) :]
            remainder = rest.split("/", 1)
            suffix = f"/{remainder[1]}" if len(remainder) == 2 else ""
            return f"{prefix}{{id}}{suffix}"
    return raw


def _sanitize_value(key: str, value: Any) -> Any:
    if key == "submission_state":
        text = str(value)
        return text if text in SUBMISSION_STATES else "unknown"
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
