# src/utils/timing.py
from __future__ import annotations

import asyncio
import math
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Type, TypeVar, ParamSpec

from src.utils.logger import get_logger

P = ParamSpec("P")
T = TypeVar("T")


# ---------------- Monotonic time helpers ----------------

def now_ms() -> int:
    """Monotonic time in milliseconds."""
    return time.monotonic_ns() // 1_000_000


def sleep_ms(ms: int) -> None:
    """Sleep for `ms` milliseconds (blocking)."""
    if ms <= 0:
        return
    time.sleep(ms / 1000.0)


async def async_sleep_ms(ms: int) -> None:
    """Async sleep for `ms` milliseconds."""
    if ms <= 0:
        return
    await asyncio.sleep(ms / 1000.0)


# ---------------- Stopwatch ----------------

@dataclass
class Stopwatch:
    """Simple stopwatch usable as a context manager."""
    start_ms: Optional[int] = None

    def start(self) -> "Stopwatch":
        self.start_ms = now_ms()
        return self

    def elapsed_ms(self) -> int:
        if self.start_ms is None:
            return 0
        return max(0, now_ms() - self.start_ms)

    def __enter__(self) -> "Stopwatch":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        # no cleanup required
        return None


# ---------------- Backoff ----------------

def exp_backoff_delays_ms(
    attempts: int,
    initial_ms: int = 200,
    factor: float = 2.0,
    max_ms: int = 5000,
    jitter: float = 0.1,
) -> Iterable[int]:
    """
    Yield `attempts` backoff delays in ms.
    Exponential growth with optional jitter (fraction of delay).
    """
    delay = max(0, initial_ms)
    for _ in range(max(1, attempts)):
        jitter_amt = delay * jitter
        if jitter_amt > 0:
            delay_j = delay + random.uniform(-jitter_amt, jitter_amt)
        else:
            delay_j = delay
        yield int(min(max_ms, max(0, delay_j)))
        # next
        delay = min(max_ms, int(math.ceil(delay * factor)))


# ---------------- Retry (sync) ----------------

def retry(
    fn: Callable[P, T],
    /,
    *args: P.args,
    exceptions: tuple[Type[BaseException], ...] = (Exception,),
    tries: int = 3,
    initial_delay_ms: int = 200,
    max_delay_ms: int = 2000,
    factor: float = 2.0,
    jitter: float = 0.1,
    before_retry: Optional[Callable[[int, BaseException], None]] = None,
    **kwargs: P.kwargs,
) -> T:
    """
    Retry a function with exponential backoff on given exceptions.

    Args:
        fn: callable to execute
        exceptions: tuple of exception types to catch
        tries: total attempts (>=1)
        initial_delay_ms, max_delay_ms, factor, jitter: backoff parameters
        before_retry: hook called as before_retry(attempt_index, exception)

    Returns:
        fn(*args, **kwargs) result on success

    Raises:
        Last caught exception after exhausting retries.
    """
    log = get_logger(__name__)
    attempts = max(1, tries)
    last_exc: Optional[BaseException] = None

    for attempt, delay in enumerate(exp_backoff_delays_ms(attempts=attempts - 1,
                                                          initial_ms=initial_delay_ms,
                                                          factor=factor,
                                                          max_ms=max_delay_ms,
                                                          jitter=jitter), start=1):
        try:
            return fn(*args, **kwargs)
        except exceptions as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt >= attempts:
                break
            if before_retry:
                try:
                    before_retry(attempt, exc)
                except Exception:
                    pass
            log.debug(f"Retry attempt {attempt}/{attempts-1} after error: {exc!r} (sleep {delay} ms)")
            sleep_ms(delay)

    # last attempt (no delay)
    try:
        return fn(*args, **kwargs)
    except exceptions as exc:  # type: ignore[misc]
        raise exc
    except BaseException:
        # if a different exception is thrown, propagate it
        raise
    finally:
        if last_exc is not None:
            # keep a breadcrumb; callers can also log on their side
            log.debug(f"Exhausted retries; last error: {last_exc!r}")


# ---------------- Retry (async) ----------------

async def async_retry(
    fn: Callable[P, Any],
    /,
    *args: P.args,
    exceptions: tuple[Type[BaseException], ...] = (Exception,),
    tries: int = 3,
    initial_delay_ms: int = 200,
    max_delay_ms: int = 2000,
    factor: float = 2.0,
    jitter: float = 0.1,
    before_retry: Optional[Callable[[int, BaseException], None]] = None,
    **kwargs: P.kwargs,
) -> Any:
    """
    Async retry with exponential backoff.
    `fn` can be an async callable.
    """
    log = get_logger(__name__)
    attempts = max(1, tries)
    last_exc: Optional[BaseException] = None

    for attempt, delay in enumerate(exp_backoff_delays_ms(attempts=attempts - 1,
                                                          initial_ms=initial_delay_ms,
                                                          factor=factor,
                                                          max_ms=max_delay_ms,
                                                          jitter=jitter), start=1):
        try:
            result = fn(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except exceptions as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt >= attempts:
                break
            if before_retry:
                try:
                    before_retry(attempt, exc)
                except Exception:
                    pass
            log.debug(f"[async] Retry attempt {attempt}/{attempts-1} after error: {exc!r} (sleep {delay} ms)")
            await async_sleep_ms(delay)

    try:
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except exceptions as exc:  # type: ignore[misc]
        raise exc
    except BaseException:
        raise
    finally:
        if last_exc is not None:
            log.debug(f"[async] Exhausted retries; last error: {last_exc!r}")


# ---------------- wait_for (polling) ----------------

def wait_for(
    predicate: Callable[[], T],
    timeout_ms: int,
    interval_ms: int = 100,
    description: Optional[str] = None,
) -> T:
    """
    Poll `predicate()` until it returns a truthy value (or any non-False value),
    or until `timeout_ms` elapses. Returns the predicate's return value.

    Raises:
        TimeoutError on timeout.
    """
    log = get_logger(__name__)
    deadline = now_ms() + max(0, timeout_ms)

    while True:
        val = predicate()
        if val:
            return val
        if now_ms() >= deadline:
            desc = f" ({description})" if description else ""
            raise TimeoutError(f"wait_for timed out after {timeout_ms} ms{desc}")
        sleep_ms(max(1, interval_ms))

        # Optional: lightweight breadcrumb if long waits
        if interval_ms >= 500 and (deadline - now_ms()) % 1000 < interval_ms:
            log.debug(f"Waiting... {max(0, deadline - now_ms())} ms left{(' - ' + description) if description else ''}")


async def async_wait_for(
    predicate: Callable[[], Any],
    timeout_ms: int,
    interval_ms: int = 100,
    description: Optional[str] = None,
) -> Any:
    """
    Async variant of wait_for(). `predicate` may be sync or async.
    """
    log = get_logger(__name__)
    deadline = now_ms() + max(0, timeout_ms)

    while True:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return result
        if now_ms() >= deadline:
            desc = f" ({description})" if description else ""
            raise TimeoutError(f"async_wait_for timed out after {timeout_ms} ms{desc}")
        await async_sleep_ms(max(1, interval_ms))

        if interval_ms >= 500 and (deadline - now_ms()) % 1000 < interval_ms:
            log.debug(f"[async] Waiting... {max(0, deadline - now_ms())} ms left{(' - ' + description) if description else ''}")


# ---------------- measure decorator ----------------

def measure(label: str = "", level: str = "INFO") -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator to log the execution time of a function.
    Example:
        @measure("open modal")
        def open_modal(...): ...
    """
    level = level.upper()
    log = get_logger(__name__)
    log_fn = getattr(log, level.lower(), log.info)

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            with Stopwatch() as sw:
                try:
                    return func(*args, **kwargs)
                finally:
                    ms = sw.elapsed_ms()
                    human = f"{ms} ms" if ms < 1000 else f"{ms/1000:.3f} s"
                    name = label or func.__name__
                    log_fn(f"{name} took {human}")
        return wrapper
    return decorator
