"""Circuit breaker and retry helper (framework-agnostic, fully unit-testable)."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the circuit is open."""


class TransientError(RuntimeError):
    """Network-level / 5xx failure that is worth retrying."""


class PermanentError(RuntimeError):
    """Failure that must not be retried (4xx, validation, bad input)."""


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return self.CLOSED
        if self._clock() - self._opened_at >= self.recovery_timeout:
            return self.HALF_OPEN
        return self.OPEN

    def allow(self) -> bool:
        state = self.state
        if state == self.CLOSED:
            return True
        if state == self.HALF_OPEN and not self._probe_in_flight:
            self._probe_in_flight = True  # exactly one trial call
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self._probe_in_flight = False
        self._failures += 1
        if self.state == self.HALF_OPEN or self._failures >= self.failure_threshold:
            self._opened_at = self._clock()


SendResult = tuple[int, dict[str, str], Any]


async def request_with_retries(
    send: Callable[[], Awaitable[SendResult]],
    *,
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_retry_after: float = 30.0,
    breaker: CircuitBreaker | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Any:
    """Run `send()` -> (status, headers, body) with retries.

    * 2xx            -> return body
    * 429            -> wait `Retry-After` (seconds, capped) and retry; not counted as a breaker failure
    * 5xx / Transient -> exponential backoff, counted as breaker failure when retries are exhausted
    * other 4xx      -> PermanentError immediately
    """
    if breaker is not None and not breaker.allow():
        raise CircuitOpenError("circuit is open")
    last_error: Exception | None = None
    rate_limited = False
    for attempt in range(max_retries + 1):
        delay = base_delay * (2**attempt)
        try:
            status, headers, body = await send()
        except TransientError as exc:
            last_error, rate_limited = exc, False
        else:
            if 200 <= status < 300:
                if breaker:
                    breaker.record_success()
                return body
            if status == 429:
                rate_limited = True
                last_error = TransientError("rate limited (429)")
                lowered = {k.lower(): v for k, v in headers.items()}
                try:
                    delay = min(float(lowered.get("retry-after", delay)), max_retry_after)
                except ValueError:
                    pass
            elif status >= 500:
                last_error, rate_limited = TransientError(f"server error {status}"), False
            else:
                if breaker:
                    breaker.record_success()  # the service is alive, request is wrong
                raise PermanentError(f"client error {status}: {body!r}"[:300])
        if attempt < max_retries:
            await sleep(delay)
    if breaker and not rate_limited:
        breaker.record_failure()
    elif breaker:
        breaker._probe_in_flight = False
    raise TransientError(f"retries exhausted: {last_error}")
