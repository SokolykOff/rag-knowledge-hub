import pytest

from app.core.resilience import CircuitBreaker, CircuitOpenError, PermanentError, TransientError, request_with_retries


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_sender(responses):
    calls = {"n": 0}

    async def send():
        r = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        if isinstance(r, Exception):
            raise r
        return r

    return send, calls


def sleeper():
    slept = []

    async def sleep(s):
        slept.append(s)

    return sleep, slept


def test_breaker_opens_half_opens_and_closes():
    clock = Clock()
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10, clock=clock)
    for _ in range(2):
        cb.record_failure()
    assert cb.state == "closed" and cb.allow()
    cb.record_failure()
    assert cb.state == "open" and not cb.allow()
    clock.t = 11
    assert cb.state == "half_open"
    assert cb.allow() and not cb.allow()  # single probe at a time
    cb.record_failure()  # probe failed -> open again immediately
    assert cb.state == "open"
    clock.t = 30
    assert cb.allow()
    cb.record_success()
    assert cb.state == "closed" and cb.allow()


async def test_retry_honours_retry_after_on_429():
    sleep, slept = sleeper()
    send, calls = make_sender([(429, {"Retry-After": "7"}, {}), (429, {"retry-after": "2"}, {}), (200, {}, {"ok": True})])
    assert await request_with_retries(send, max_retries=3, base_delay=0.5, sleep=sleep) == {"ok": True}
    assert slept == [7.0, 2.0] and calls["n"] == 3


async def test_retry_after_is_capped_and_429_does_not_trip_breaker():
    sleep, slept = sleeper()
    cb = CircuitBreaker(failure_threshold=1)
    send, _ = make_sender([(429, {"Retry-After": "9999"}, {})])
    with pytest.raises(TransientError):
        await request_with_retries(send, max_retries=1, sleep=sleep, breaker=cb, max_retry_after=30)
    assert slept == [30.0] and cb.state == "closed"


async def test_5xx_exponential_backoff_then_breaker_opens():
    sleep, slept = sleeper()
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60)
    send, calls = make_sender([(503, {}, {})])
    for _ in range(2):
        with pytest.raises(TransientError):
            await request_with_retries(send, max_retries=2, base_delay=1.0, sleep=sleep, breaker=cb)
    assert slept[:2] == [1.0, 2.0] and calls["n"] == 6
    assert cb.state == "open"
    with pytest.raises(CircuitOpenError):  # rejected without touching the network
        await request_with_retries(send, sleep=sleep, breaker=cb)
    assert calls["n"] == 6


async def test_network_errors_retried_and_4xx_permanent():
    sleep, _ = sleeper()
    send, calls = make_sender([TransientError("timeout"), (200, {}, {"r": 1})])
    assert await request_with_retries(send, sleep=sleep) == {"r": 1} and calls["n"] == 2
    send, calls = make_sender([(400, {}, {"error": "bad"})])
    with pytest.raises(PermanentError):
        await request_with_retries(send, sleep=sleep)
    assert calls["n"] == 1
