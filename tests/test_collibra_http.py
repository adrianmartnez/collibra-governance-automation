"""Bounded Collibra HTTP retry policy tests (no real sleeps)."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from governance.config import Settings
from governance.integrations.collibra import (
    CollibraAdapterError,
    CollibraDesiredState,
    LiveCollibraAdapter,
    mock_mapping_config,
)
from governance.integrations.collibra import live as live_module
from governance.integrations.collibra.http import (
    MAX_PAGINATION_PAGES,
    CollibraHttpExecutor,
    RetryPolicy,
    parse_retry_after_seconds,
)


class _Clocks:
    def __init__(self, *, mono: float = 0.0, wall: float = 1_700_000_000.0) -> None:
        self.mono = mono
        self.wall = wall
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.mono

    def wall_clock(self) -> float:
        return self.wall

    def sleeper(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.mono += seconds
        self.wall += seconds


def _settings() -> Settings:
    return Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="governance_demo",
        postgres_user="postgres",
        postgres_password="postgres",
        postgres_source_name="governance-demo",
        inventory_output_path="artifacts/metadata-inventory.json",
        collibra_mode="live",
        collibra_base_url="https://collibra.example.invalid",
        collibra_bearer_token="token",
    )


def _executor(
    handler: Any,
    clocks: _Clocks,
    *,
    policy: RetryPolicy | None = None,
) -> CollibraHttpExecutor:
    client = httpx.Client(
        base_url="https://collibra.example.invalid",
        transport=httpx.MockTransport(handler),
    )
    return CollibraHttpExecutor(
        client,
        policy=policy,
        monotonic_clock=clocks.monotonic,
        wall_clock=clocks.wall_clock,
        sleeper=clocks.sleeper,
    )


def test_parse_retry_after_integer_zero_and_http_date() -> None:
    assert parse_retry_after_seconds("12", wall_now=0.0) == 12.0
    assert parse_retry_after_seconds("0", wall_now=100.0) == 0.0
    future = datetime.fromtimestamp(1_700_000_030, tz=UTC)
    header = format_datetime(future)
    assert parse_retry_after_seconds(header, wall_now=1_700_000_000.0) == pytest.approx(30.0)
    past = datetime.fromtimestamp(1_699_999_000, tz=UTC)
    assert parse_retry_after_seconds(format_datetime(past), wall_now=1_700_000_000.0) == 0.0
    assert parse_retry_after_seconds("not-a-date", wall_now=0.0) is None
    assert parse_retry_after_seconds("-1", wall_now=0.0) is None


def test_get_retries_429_with_integer_retry_after() -> None:
    clocks = _Clocks()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "slow"})
        return httpx.Response(200, json={"ok": True})

    executor = _executor(handler, clocks)
    response = executor.request("GET", "/rest/2.0/assets")
    assert response.status_code == 200
    assert clocks.sleeps == [7.0]
    assert calls["n"] == 2


def test_retry_after_zero_counts_attempt() -> None:
    clocks = _Clocks()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={})

    executor = _executor(handler, clocks)
    assert executor.request("GET", "/x").status_code == 200
    assert clocks.sleeps == [0.0, 0.0]


def test_retry_after_http_date_future_and_past() -> None:
    clocks = _Clocks()
    future = datetime.fromtimestamp(clocks.wall + 11, tz=UTC)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": format_datetime(future)})
        return httpx.Response(200, json={})

    executor = _executor(handler, clocks)
    assert executor.request("GET", "/x").status_code == 200
    assert clocks.sleeps == [pytest.approx(11.0)]

    clocks = _Clocks()
    past = datetime.fromtimestamp(clocks.wall - 30, tz=UTC)
    calls = {"n": 0}

    def handler_past(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": format_datetime(past)})
        return httpx.Response(200, json={})

    executor = _executor(handler_past, clocks)
    assert executor.request("GET", "/x").status_code == 200
    assert clocks.sleeps == [0.0]


def test_malformed_retry_after_uses_exponential_backoff() -> None:
    clocks = _Clocks()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "soonish"})
        return httpx.Response(200, json={})

    executor = _executor(handler, clocks)
    assert executor.request("GET", "/x").status_code == 200
    assert clocks.sleeps == [0.5]


def test_retry_after_capped_to_remaining_max_elapsed() -> None:
    clocks = _Clocks()
    policy = RetryPolicy(max_attempts=4, max_elapsed_seconds=10.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "1000"})

    executor = _executor(handler, clocks, policy=policy)
    with pytest.raises(CollibraAdapterError) as exc_info:
        executor.request("GET", "/x")
    assert exc_info.value.exhausted is True
    assert exc_info.value.status_code == 429
    assert clocks.sleeps[0] == 10.0


def test_exhaustion_remaining_zero() -> None:
    clocks = _Clocks(mono=10.0)
    policy = RetryPolicy(max_attempts=4, max_elapsed_seconds=10.0)
    # started at 10, max_elapsed 10 → remaining 0 after first failure

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        clocks.mono = 20.0
        return httpx.Response(429, headers={"Retry-After": "1"})

    executor = _executor(handler, clocks, policy=policy)
    with pytest.raises(CollibraAdapterError) as exc_info:
        executor.request("GET", "/x")
    assert exc_info.value.exhausted is True
    assert clocks.sleeps == []


def test_get_retries_5xx_write_does_not() -> None:
    clocks = _Clocks()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": "down"})
        return httpx.Response(200, json={})

    executor = _executor(handler, clocks)
    assert executor.request("GET", "/x").status_code == 200
    assert calls["n"] == 3

    clocks = _Clocks()
    write_calls = {"n": 0}

    def write_handler(request: httpx.Request) -> httpx.Response:
        del request
        write_calls["n"] += 1
        return httpx.Response(503, json={"error": "down"})

    executor = _executor(write_handler, clocks)
    with pytest.raises(CollibraAdapterError) as exc_info:
        executor.request("POST", "/x", json={"a": 1})
    assert exc_info.value.status_code == 503
    assert exc_info.value.exhausted is False
    assert write_calls["n"] == 1
    assert clocks.sleeps == []


def test_connect_error_retries_for_writes() -> None:
    clocks = _Clocks()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("nope")
        return httpx.Response(201, json={"id": "1"})

    executor = _executor(handler, clocks)
    assert executor.request("POST", "/x", json={}).status_code == 201
    assert calls["n"] == 2
    assert clocks.sleeps == [0.5]


def test_read_timeout_retries_get_not_write() -> None:
    clocks = _Clocks()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json={})

    executor = _executor(handler, clocks)
    assert executor.request("GET", "/x").status_code == 200

    clocks = _Clocks()
    write_calls = {"n": 0}

    def write_handler(request: httpx.Request) -> httpx.Response:
        del request
        write_calls["n"] += 1
        raise httpx.ReadTimeout("slow")

    executor = _executor(write_handler, clocks)
    with pytest.raises(CollibraAdapterError) as exc_info:
        executor.request("PATCH", "/x", json={})
    assert exc_info.value.exhausted is False
    assert write_calls["n"] == 1


def test_401_and_4xx_are_not_retried_by_executor() -> None:
    clocks = _Clocks()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(401, json={"error": "auth"})

    executor = _executor(handler, clocks)
    assert executor.request("GET", "/x").status_code == 401
    assert calls["n"] == 1
    assert clocks.sleeps == []

    calls = {"n": 0}

    def handler_400(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad"})

    executor = _executor(handler_400, clocks)
    assert executor.request("POST", "/x", json={}).status_code == 400
    assert calls["n"] == 1


def test_max_attempts_exhausted() -> None:
    clocks = _Clocks()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(503)

    executor = _executor(handler, clocks)
    with pytest.raises(CollibraAdapterError) as exc_info:
        executor.request("GET", "/x")
    assert calls["n"] == 4
    assert exc_info.value.attempt == 4
    assert exc_info.value.exhausted is True
    assert exc_info.value.endpoint_family == "core_rest"
    assert "Retry-After" not in str(exc_info.value)


def test_live_get_429_then_success_without_real_sleep() -> None:
    clocks = _Clocks()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url)
        if "/assets" in path and request.method == "GET":
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "1"}, json={})
            return httpx.Response(200, json={"results": []})
        if request.method == "GET":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404)

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
        monotonic_clock=clocks.monotonic,
        wall_clock=clocks.wall_clock,
        sleeper=clocks.sleeper,
    )
    state = adapter.read_remote_state(CollibraDesiredState(assets=()))
    assert state.assets == ()
    assert clocks.sleeps == [1.0]


def test_pagination_hard_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_module, "MAX_PAGINATION_PAGES", 2)
    clocks = _Clocks()

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={"results": [{"id": "a", "domain": {"id": "x"}, "type": {"id": "y"}}]},
        )

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
        monotonic_clock=clocks.monotonic,
        wall_clock=clocks.wall_clock,
        sleeper=clocks.sleeper,
        page_size=1,
    )
    with pytest.raises(CollibraAdapterError, match="pagination exceeded"):
        adapter.read_remote_state(CollibraDesiredState(assets=()))
    assert MAX_PAGINATION_PAGES == 10_000
