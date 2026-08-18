"""Bounded, deterministic Collibra HTTP retry policy."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from governance.integrations.collibra.adapters import CollibraAdapterError

QueryParams = Mapping[str, Any] | Sequence[tuple[str, Any]]

DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_MAX_ELAPSED_SECONDS = 60.0
DEFAULT_INITIAL_BACKOFF_SECONDS = 0.5
DEFAULT_MAX_BACKOFF_SECONDS = 8.0
MAX_PAGINATION_PAGES = 10_000
ENDPOINT_FAMILY_CORE_REST = "core_rest"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_elapsed_seconds: float = DEFAULT_MAX_ELAPSED_SECONDS
    initial_backoff_seconds: float = DEFAULT_INITIAL_BACKOFF_SECONDS
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS

    def backoff_seconds(self, attempt: int) -> float:
        """Exponential backoff for the given 1-based failed attempt number."""
        delay = self.initial_backoff_seconds * (2 ** (attempt - 1))
        return min(self.max_backoff_seconds, delay)


@dataclass(frozen=True, slots=True)
class RetryState:
    attempt: int
    elapsed_seconds: float
    exhausted: bool


def parse_retry_after_seconds(value: str | None, *, wall_now: float) -> float | None:
    """Return a Retry-After delay, or None when the header is absent/malformed."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.isdigit():
        return float(int(raw))
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError, IndexError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delay = parsed.timestamp() - wall_now
    return max(0.0, delay)


class CollibraHttpExecutor:
    """Executes Core REST calls with bounded retries. Does not acquire OAuth tokens."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        policy: RetryPolicy | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        endpoint_family: str = ENDPOINT_FAMILY_CORE_REST,
    ) -> None:
        self._client = client
        self._policy = policy or RetryPolicy()
        self._monotonic = monotonic_clock or time.monotonic
        self._wall = wall_clock or time.time
        self._sleep = sleeper or time.sleep
        self._endpoint_family = endpoint_family

    def request(
        self,
        method: str,
        path: str,
        *,
        params: QueryParams | None = None,
        json: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        auth: httpx.Auth | None = None,
        operation: str | None = None,
    ) -> httpx.Response:
        op = operation or method.lower()
        is_write = method.upper() in {"POST", "PATCH", "PUT", "DELETE"}
        started = self._monotonic()
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    headers=headers,
                    auth=auth,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if not self._should_retry(attempt=attempt, started=started):
                    raise self._transport_error(
                        op,
                        path,
                        attempt=attempt,
                        started=started,
                        exhausted=True,
                    ) from None
                self._sleep(self._delay_for_backoff(attempt=attempt, started=started))
                continue
            except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
                if is_write:
                    raise self._transport_error(
                        op,
                        path,
                        attempt=attempt,
                        started=started,
                        exhausted=False,
                    ) from None
                if not self._should_retry(attempt=attempt, started=started):
                    raise self._transport_error(
                        op,
                        path,
                        attempt=attempt,
                        started=started,
                        exhausted=True,
                    ) from None
                self._sleep(self._delay_for_backoff(attempt=attempt, started=started))
                continue
            except httpx.HTTPError:
                raise self._transport_error(
                    op,
                    path,
                    attempt=attempt,
                    started=started,
                    exhausted=False,
                ) from None

            if response.status_code == 429:
                if not self._should_retry(attempt=attempt, started=started):
                    raise self._http_error(
                        op,
                        path,
                        status_code=429,
                        attempt=attempt,
                        started=started,
                        exhausted=True,
                    )
                delay = parse_retry_after_seconds(
                    response.headers.get("Retry-After"),
                    wall_now=self._wall(),
                )
                if delay is None:
                    delay = self._policy.backoff_seconds(attempt)
                capped = self._cap_delay(delay, started=started)
                if capped is None:
                    raise self._http_error(
                        op,
                        path,
                        status_code=429,
                        attempt=attempt,
                        started=started,
                        exhausted=True,
                    )
                self._sleep(capped)
                continue

            if 500 <= response.status_code <= 599:
                if is_write:
                    raise self._http_error(
                        op,
                        path,
                        status_code=response.status_code,
                        attempt=attempt,
                        started=started,
                        exhausted=False,
                    )
                if not self._should_retry(attempt=attempt, started=started):
                    raise self._http_error(
                        op,
                        path,
                        status_code=response.status_code,
                        attempt=attempt,
                        started=started,
                        exhausted=True,
                    )
                self._sleep(self._delay_for_backoff(attempt=attempt, started=started))
                continue

            return response

    def _should_retry(self, *, attempt: int, started: float) -> bool:
        if attempt >= self._policy.max_attempts:
            return False
        elapsed = self._monotonic() - started
        return elapsed < self._policy.max_elapsed_seconds

    def _delay_for_backoff(self, *, attempt: int, started: float) -> float:
        delay = self._policy.backoff_seconds(attempt)
        capped = self._cap_delay(delay, started=started)
        return 0.0 if capped is None else capped

    def _cap_delay(self, delay: float, *, started: float) -> float | None:
        remaining = self._policy.max_elapsed_seconds - (self._monotonic() - started)
        if remaining <= 0:
            return None
        return min(delay, remaining)

    def _transport_error(
        self,
        operation: str,
        path: str,
        *,
        attempt: int,
        started: float,
        exhausted: bool,
    ) -> CollibraAdapterError:
        del started
        return CollibraAdapterError(
            "HTTP transport error",
            operation=operation,
            endpoint_path=path,
            endpoint_family=self._endpoint_family,
            attempt=attempt,
            exhausted=exhausted,
        )

    def _http_error(
        self,
        operation: str,
        path: str,
        *,
        status_code: int,
        attempt: int,
        started: float,
        exhausted: bool,
    ) -> CollibraAdapterError:
        del started
        return CollibraAdapterError(
            "Collibra API request failed",
            operation=operation,
            status_code=status_code,
            endpoint_path=path,
            endpoint_family=self._endpoint_family,
            attempt=attempt,
            exhausted=exhausted,
        )
