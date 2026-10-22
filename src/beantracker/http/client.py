import asyncio
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from beantracker.http.throttle import DomainThrottle

RETRYABLE = {429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    pass


class PoliteClient:
    """httpx wrapper: robots.txt, per-host rate limit, retry with backoff, optional disk cache.

    The cache is a development convenience (avoid hammering sites while iterating on parsers);
    it is off unless cache_dir is given.
    """

    def __init__(
        self,
        user_agent: str,
        delay_s: float = 2.0,
        cache_dir: Path | None = None,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        throttle: DomainThrottle | None = None,
    ) -> None:
        self._ua = user_agent
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=30,
            follow_redirects=True,
            transport=transport,
        )
        self._throttle = throttle or DomainThrottle(delay_s)
        self._cache_dir = cache_dir
        self._max_retries = max_retries
        self._robots: dict[str, RobotFileParser] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(origin)
        if parser is None:
            parser = RobotFileParser()
            resp = await self._request(f"{origin}/robots.txt", check_robots=False)
            if resp.status_code in (401, 403):
                parser.parse(["User-agent: *", "Disallow: /"])  # RFC 9309: denied means stay out
            elif resp.status_code >= 400:
                parser.parse(["User-agent: *", "Allow: /"])  # no robots.txt: no restrictions
            else:
                parser.parse(resp.text.splitlines())
            self._robots[origin] = parser
        return parser.can_fetch(self._ua, url)

    def _cache_path(self, url: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / hashlib.sha256(url.encode()).hexdigest()

    async def _request(self, url: str, *, check_robots: bool = True) -> httpx.Response:
        if check_robots and not await self._allowed(url):
            raise RobotsDisallowed(url)
        host = urlsplit(url).netloc
        for attempt in range(self._max_retries + 1):
            await self._throttle.wait(host)
            resp = await self._client.get(url)
            if resp.status_code not in RETRYABLE or attempt == self._max_retries:
                return resp
            # Honor Retry-After when numeric, else exponential backoff.
            retry_after = resp.headers.get("Retry-After", "")
            delay = float(retry_after) if retry_after.isdigit() else 2.0**attempt
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def get_text(self, url: str) -> str:
        cached = self._cache_path(url)
        if cached is not None and cached.exists():
            return cached.read_text(encoding="utf-8")
        resp = await self._request(url)
        resp.raise_for_status()
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(resp.text, encoding="utf-8")
        return resp.text
