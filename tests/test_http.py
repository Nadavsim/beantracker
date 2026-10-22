from pathlib import Path

import httpx
import pytest
import respx

from beantracker.http.client import PoliteClient, RobotsDisallowed
from beantracker.http.throttle import DomainThrottle

UA = "beantracker-test/0.1"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s


async def test_throttle_spaces_requests_per_host() -> None:
    clock = FakeClock()
    t = DomainThrottle(2.0, clock=clock.time, sleep=clock.sleep)
    await t.wait("a.test")
    await t.wait("a.test")
    await t.wait("b.test")  # different host: no wait
    assert clock.slept == [2.0]


def make_client(**kw: object) -> PoliteClient:
    clock = FakeClock()
    throttle = DomainThrottle(0, clock=clock.time, sleep=clock.sleep)
    return PoliteClient(UA, throttle=throttle, **kw)  # type: ignore[arg-type]


@respx.mock
async def test_robots_disallow_blocks_fetch() -> None:
    respx.get("https://x.test/robots.txt").respond(text="User-agent: *\nDisallow: /private")
    page = respx.get("https://x.test/private/a")
    client = make_client()
    with pytest.raises(RobotsDisallowed):
        await client.get_text("https://x.test/private/a")
    assert not page.called
    await client.aclose()


@respx.mock
async def test_missing_robots_allows_and_sends_user_agent() -> None:
    respx.get("https://x.test/robots.txt").respond(404)
    route = respx.get("https://x.test/ok").respond(text="hi")
    client = make_client()
    assert await client.get_text("https://x.test/ok") == "hi"
    assert route.calls.last.request.headers["User-Agent"] == UA
    await client.aclose()


@respx.mock
async def test_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("beantracker.http.client.asyncio.sleep", no_sleep)
    respx.get("https://x.test/robots.txt").respond(404)
    route = respx.get("https://x.test/p").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, text="ok"),
        ]
    )
    client = make_client()
    assert await client.get_text("https://x.test/p") == "ok"
    assert route.call_count == 2
    await client.aclose()


@respx.mock
async def test_disk_cache_avoids_second_request(tmp_path: Path) -> None:
    respx.get("https://x.test/robots.txt").respond(404)
    route = respx.get("https://x.test/c").respond(text="body")
    client = make_client(cache_dir=tmp_path)
    await client.get_text("https://x.test/c")
    await client.get_text("https://x.test/c")
    assert route.call_count == 1
    await client.aclose()
