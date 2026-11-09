import asyncio
import time
from collections.abc import Awaitable, Callable


class DomainThrottle:
    """Enforces a minimum gap between requests to the same host.

    A per-host lock serializes callers so concurrent tasks cannot burst past the delay.
    """

    def __init__(
        self,
        delay_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._delay = delay_s
        self._clock = clock
        self._sleep = sleep
        self._host_delay: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    def require_delay(self, host: str, delay_s: float) -> None:
        """A site's own Crawl-delay: it can only slow a host down, never speed it up."""
        self._host_delay[host] = max(delay_s, self._host_delay.get(host, 0.0))

    async def wait(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last.get(host)
            if last is not None:
                delay = max(self._delay, self._host_delay.get(host, 0.0))
                remaining = delay - (self._clock() - last)
                if remaining > 0:
                    await self._sleep(remaining)
            self._last[host] = self._clock()
