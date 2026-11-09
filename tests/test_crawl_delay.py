import httpx  # noqa: F401  (respx patches httpx)
import pytest
import respx

from beantracker.http.client import PoliteClient, RobotsDisallowed
from beantracker.http.robots import Robots
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


def test_crawl_delay_of_the_named_group_or_the_wildcard_group() -> None:
    txt = (
        "User-agent: *\nCrawl-delay: 10\nDisallow: /x\n\n"
        "User-agent: beantracker\nCrawl-delay: 3\n\n"
        "User-agent: ahrefsbot\nCrawl-delay: 99"
    )
    robots = Robots.parse(txt)
    assert robots.crawl_delay("beantracker") == 3  # its own group wins
    assert robots.crawl_delay("otherbot") == 10  # falls back to the wildcard group
    assert Robots.parse("User-agent: *\nDisallow: /x").crawl_delay("beantracker") is None


@pytest.mark.parametrize("value", ["soon", "0", "-5", ""])
def test_unparseable_zero_or_negative_crawl_delay_is_ignored(value: str) -> None:
    robots = Robots.parse(f"User-agent: *\nCrawl-delay: {value}")
    assert robots.crawl_delay("beantracker") is None


async def test_throttle_honours_a_hosts_own_delay_but_never_speeds_up() -> None:
    clock = FakeClock()
    t = DomainThrottle(2.0, clock=clock.time, sleep=clock.sleep)
    t.require_delay("slow.test", 5.0)
    t.require_delay("fast.test", 0.5)  # lower than our own 2 s: ignored
    for host in ("slow.test", "slow.test", "fast.test", "fast.test"):
        await t.wait(host)
    assert clock.slept == [5.0, 2.0]


@respx.mock
async def test_crawl_delay_in_robots_slows_requests_to_that_host() -> None:
    respx.get("https://x.test/robots.txt").respond(text="User-agent: *\nCrawl-delay: 5")
    respx.get("https://x.test/a").respond(text="a")
    respx.get("https://x.test/b").respond(text="b")
    clock = FakeClock()
    client = PoliteClient(UA, throttle=DomainThrottle(1.0, clock=clock.time, sleep=clock.sleep))
    await client.get_text("https://x.test/a")
    await client.get_text("https://x.test/b")
    assert clock.slept == [5.0, 5.0]  # robots -> a, then a -> b: both gaps are the site's 5 s
    await client.aclose()


@respx.mock
async def test_crawl_delay_over_our_limit_makes_the_site_off_limits() -> None:
    respx.get("https://x.test/robots.txt").respond(text="User-agent: *\nCrawl-delay: 120")
    page = respx.get("https://x.test/p")
    clock = FakeClock()
    client = PoliteClient(
        UA,
        throttle=DomainThrottle(0, clock=clock.time, sleep=clock.sleep),
        max_crawl_delay_s=30,
    )
    with pytest.raises(RobotsDisallowed, match="crawl-delay"):
        await client.get_text("https://x.test/p")
    assert not page.called
    await client.aclose()
