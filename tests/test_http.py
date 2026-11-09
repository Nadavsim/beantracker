import os
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
import respx

from beantracker.http.client import PoliteClient, RobotsDisallowed, TooManyRedirects
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


async def _no_sleep(_: float) -> None:
    return None


@respx.mock
@pytest.mark.parametrize("status", [500, 503, 403, 401, 429])
async def test_unreadable_robots_means_off_limits(status: int) -> None:
    respx.get("https://x.test/robots.txt").respond(status)
    page = respx.get("https://x.test/p")
    client = make_client(max_retries=0)
    with pytest.raises(RobotsDisallowed, match=f"HTTP {status}"):
        await client.get_text("https://x.test/p")
    assert not page.called
    await client.aclose()


@respx.mock
async def test_unreachable_robots_means_off_limits() -> None:
    respx.get("https://x.test/robots.txt").mock(side_effect=httpx.ConnectError("down"))
    page = respx.get("https://x.test/p")
    client = make_client(max_retries=0)
    with pytest.raises(RobotsDisallowed, match="unreachable"):
        await client.get_text("https://x.test/p")
    assert not page.called
    await client.aclose()


@respx.mock
async def test_other_4xx_on_robots_means_no_restrictions() -> None:
    respx.get("https://x.test/robots.txt").respond(410)
    respx.get("https://x.test/p").respond(text="ok")
    client = make_client()
    assert await client.get_text("https://x.test/p") == "ok"
    await client.aclose()


@respx.mock
async def test_wildcard_rules_in_robots_are_enforced() -> None:
    respx.get("https://x.test/robots.txt").respond(text="User-agent: *\nDisallow: /*?sort_by")
    plain = respx.get("https://x.test/c").respond(text="ok")
    sorted_ = respx.get("https://x.test/c", params={"sort_by": "price"}).respond(text="no")
    client = make_client()
    assert await client.get_text("https://x.test/c") == "ok"
    with pytest.raises(RobotsDisallowed):
        await client.get_text("https://x.test/c?sort_by=price")
    assert plain.called
    assert not sorted_.called
    await client.aclose()


@respx.mock
async def test_redirect_to_another_host_is_checked_against_that_hosts_robots() -> None:
    respx.get("https://a.test/robots.txt").respond(404)
    respx.get("https://a.test/start").respond(301, headers={"Location": "https://b.test/page"})
    respx.get("https://b.test/robots.txt").respond(text="User-agent: *\nDisallow: /page")
    target = respx.get("https://b.test/page")
    client = make_client()
    with pytest.raises(RobotsDisallowed, match="b.test"):
        await client.get_text("https://a.test/start")
    assert not target.called
    await client.aclose()


@respx.mock
async def test_allowed_redirect_is_followed() -> None:
    respx.get("https://a.test/robots.txt").respond(404)
    respx.get("https://a.test/old").respond(301, headers={"Location": "/new"})
    respx.get("https://a.test/new").respond(text="moved")
    client = make_client()
    assert await client.get_text("https://a.test/old") == "moved"
    await client.aclose()


@respx.mock
async def test_redirect_loop_is_stopped() -> None:
    respx.get("https://a.test/robots.txt").respond(404)
    respx.get("https://a.test/loop").respond(302, headers={"Location": "/loop"})
    client = make_client()
    with pytest.raises(TooManyRedirects):
        await client.get_text("https://a.test/loop")
    await client.aclose()


@respx.mock
async def test_network_error_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("beantracker.http.client.asyncio.sleep", _no_sleep)
    respx.get("https://x.test/robots.txt").respond(404)
    route = respx.get("https://x.test/p").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, text="ok")]
    )
    client = make_client()
    assert await client.get_text("https://x.test/p") == "ok"
    assert route.call_count == 2
    await client.aclose()


@respx.mock
async def test_network_error_is_raised_after_the_retries_run_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("beantracker.http.client.asyncio.sleep", _no_sleep)
    respx.get("https://x.test/robots.txt").respond(404)
    route = respx.get("https://x.test/p").mock(side_effect=httpx.ConnectError("down"))
    client = make_client(max_retries=2)
    with pytest.raises(httpx.ConnectError):
        await client.get_text("https://x.test/p")
    assert route.call_count == 3
    await client.aclose()


@respx.mock
async def test_huge_retry_after_is_not_obeyed(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("beantracker.http.client.asyncio.sleep", record)
    respx.get("https://x.test/robots.txt").respond(404)
    route = respx.get("https://x.test/p").respond(429, headers={"Retry-After": "86400"})
    client = make_client()
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_text("https://x.test/p")
    assert route.call_count == 1  # gave up instead of sleeping a day
    assert slept == []
    await client.aclose()


@respx.mock
async def test_retry_after_as_an_http_date(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("beantracker.http.client.asyncio.sleep", record)
    respx.get("https://x.test/robots.txt").respond(404)
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    respx.get("https://x.test/p").mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": when}),
            httpx.Response(200, text="ok"),
        ]
    )
    client = make_client()
    assert await client.get_text("https://x.test/p") == "ok"
    assert len(slept) == 1
    assert 25 <= slept[0] <= 30
    await client.aclose()


@respx.mock
async def test_cache_entries_expire(tmp_path: Path) -> None:
    respx.get("https://x.test/robots.txt").respond(404)
    route = respx.get("https://x.test/c").respond(text="body")
    client = make_client(cache_dir=tmp_path, cache_ttl_s=3600)
    await client.get_text("https://x.test/c")
    await client.get_text("https://x.test/c")  # fresh: served from disk
    assert route.call_count == 1

    old = time.time() - 7200
    for name in os.listdir(tmp_path):  # age the cached page past the TTL
        os.utime(tmp_path / name, (old, old))
    await client.get_text("https://x.test/c")
    assert route.call_count == 2
    await client.aclose()
