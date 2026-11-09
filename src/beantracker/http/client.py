import asyncio
import hashlib
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from beantracker.http.robots import Robots, agent_token
from beantracker.http.throttle import DomainThrottle

RETRYABLE = {429, 500, 502, 503, 504}
MAX_REDIRECTS = 5


class RobotsDisallowed(Exception):
    """The URL is off limits: robots.txt forbids it, or could not be read to say otherwise."""


class TooManyRedirects(Exception):
    pass


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying: Retry-After (seconds or HTTP date), else 1, 2, 4..."""
    value = resp.headers.get("Retry-After", "").strip()
    if value.isdigit():
        return float(value)
    if value:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return 2.0**attempt
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())
    return 2.0**attempt


class PoliteClient:
    """httpx wrapper: robots.txt, per-host rate limit, retries, optional disk cache.

    Every hop, including each redirect, is checked against the robots.txt of the host it
    lands on and rate limited per host. The cache is a development convenience for iterating
    on parsers: entries expire after `cache_ttl_s`, and the CLI never lets cached pages feed
    the database.
    """

    def __init__(
        self,
        user_agent: str,
        delay_s: float = 2.0,
        cache_dir: Path | None = None,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        throttle: DomainThrottle | None = None,
        cache_ttl_s: float | None = None,
        max_retry_wait_s: float = 60.0,
    ) -> None:
        self._token = agent_token(user_agent)
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=30,
            follow_redirects=False,  # followed by hand so each hop is checked
            transport=transport,
        )
        self._throttle = throttle or DomainThrottle(delay_s)
        self._cache_dir = cache_dir
        self._cache_ttl_s = cache_ttl_s
        self._max_retries = max_retries
        self._max_retry_wait_s = max_retry_wait_s
        self._robots: dict[str, tuple[Robots, str]] = {}  # origin -> (rules, why if blanket)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _robots_for(self, origin: str) -> tuple[Robots, str]:
        """Rules for a site, fetched once. When robots.txt cannot be read, we cannot know what
        is allowed, so for server errors, rate limiting, access denied and network failures
        the whole site is treated as off limits (a missing file, any other 4xx, means no
        restrictions)."""
        if origin not in self._robots:
            try:
                resp = await self._fetch(f"{origin}/robots.txt", check_robots=False)
            except (httpx.HTTPError, TooManyRedirects) as exc:
                result = (Robots.disallow_all(), f"robots.txt unreachable ({type(exc).__name__})")
            else:
                status = resp.status_code
                if 200 <= status < 300:
                    result = (Robots.parse(resp.text), "")
                elif status in (401, 403, 429) or status >= 500:
                    result = (Robots.disallow_all(), f"robots.txt unavailable (HTTP {status})")
                else:
                    result = (Robots.allow_all(), "")
            self._robots[origin] = result
        return self._robots[origin]

    async def _check_allowed(self, url: str) -> None:
        parts = urlsplit(url)
        robots, why = await self._robots_for(f"{parts.scheme}://{parts.netloc}")
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        if not robots.allowed(self._token, path):
            raise RobotsDisallowed(f"{url}: {why or 'disallowed by robots.txt'}")

    def _cache_path(self, url: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / hashlib.sha256(url.encode()).hexdigest()

    def _fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        return self._cache_ttl_s is None or time.time() - path.stat().st_mtime <= self._cache_ttl_s

    async def _get_with_retries(self, url: str) -> httpx.Response:
        host = urlsplit(url).netloc
        for attempt in range(self._max_retries + 1):
            await self._throttle.wait(host)
            try:
                resp = await self._client.get(url)
            except httpx.TransportError:  # timeout, connection reset, DNS failure
                if attempt == self._max_retries:
                    raise
                await asyncio.sleep(2.0**attempt)
                continue
            if resp.status_code not in RETRYABLE or attempt == self._max_retries:
                return resp
            delay = _retry_delay(resp, attempt)
            if delay > self._max_retry_wait_s:
                return resp  # the server wants a longer wait than we will give: report the error
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def _fetch(self, url: str, *, check_robots: bool = True) -> httpx.Response:
        for _ in range(MAX_REDIRECTS + 1):
            if check_robots:
                await self._check_allowed(url)
            resp = await self._get_with_retries(url)
            location = resp.headers.get("Location")
            if resp.is_redirect and location:
                url = urljoin(url, location)
                continue
            return resp
        raise TooManyRedirects(url)

    async def get_text(self, url: str) -> str:
        cached = self._cache_path(url)
        if cached is not None and self._fresh(cached):
            return cached.read_text(encoding="utf-8")
        resp = await self._fetch(url)
        resp.raise_for_status()
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(resp.text, encoding="utf-8")
        return resp.text
